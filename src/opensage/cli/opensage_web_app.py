from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, List, Literal, Optional
from urllib.parse import urlencode

import graphviz
import pydantic
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.live_request_queue import LiveRequest, LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.apps.app import App
from google.adk.cli import agent_graph
from google.adk.cli.adk_web_server import (
    CreateSessionRequest,
    GetEventGraphResult,
    ListEvalResultsResponse,
    ListEvalSetsResponse,
    RunAgentRequest,
)
from google.adk.events.event import Event
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import Runner
from google.adk.utils.context_utils import Aclosing
from google.genai import types
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace import export as export_lib
from pydantic import ValidationError
from starlette.types import Lifespan

from opensage.memory.file_based.short_term import build_root_session_state

logger = logging.getLogger("opensage." + __name__)


class UpdateSessionEventRequest(pydantic.BaseModel):
    """Request to update one editable content part in a session event."""

    model_config = pydantic.ConfigDict(
        alias_generator=pydantic.alias_generators.to_camel,
        populate_by_name=True,
    )

    part_index: Optional[int] = None
    text: Optional[str] = None
    function_call: Optional[types.FunctionCall] = None
    function_response: Optional[types.FunctionResponse] = None


def _replace_event_part(event: Event, req: UpdateSessionEventRequest) -> Event:
    """Return a copy of an event with one editable part replaced."""
    if not event.content or not event.content.parts:
        raise ValueError("Only events with content parts can be edited.")

    updated_event = event.model_copy(deep=True)
    parts = updated_event.content.parts

    if req.text is not None:
        if req.part_index is None:
            target_index = next(
                (
                    index
                    for index, part in enumerate(parts)
                    if part.text is not None and not part.thought
                ),
                None,
            )
        else:
            target_index = req.part_index

        if target_index is None or not (0 <= target_index < len(parts)):
            raise ValueError("The requested text part does not exist.")
        if parts[target_index].text is None or parts[target_index].thought:
            raise ValueError("Only non-thought text parts can be edited.")
        parts[target_index].text = req.text
        return updated_event

    if req.function_call is not None:
        if req.part_index is None or not (0 <= req.part_index < len(parts)):
            raise ValueError("A valid part_index is required for tool call edits.")
        if parts[req.part_index].function_call is None:
            raise ValueError("The requested content part is not a tool call.")
        parts[req.part_index].function_call = req.function_call
        return updated_event

    if req.function_response is not None:
        if req.part_index is None or not (0 <= req.part_index < len(parts)):
            raise ValueError("A valid part_index is required for tool response edits.")
        if parts[req.part_index].function_response is None:
            raise ValueError("The requested content part is not a tool response.")
        parts[req.part_index].function_response = req.function_response
        return updated_event

    raise ValueError(
        "One of text, function_call, or function_response must be provided."
    )


class _InMemoryExporter(export_lib.SpanExporter):
    def __init__(self, trace_dict):
        super().__init__()
        self._spans = []
        self.trace_dict = trace_dict

    def export(self, spans) -> export_lib.SpanExportResult:
        for span in spans:
            trace_id = span.context.trace_id
            if span.name == "call_llm":
                attributes = dict(span.attributes)
                session_id = attributes.get("gcp.vertex.agent.session_id", None)
                if session_id:
                    if session_id not in self.trace_dict:
                        self.trace_dict[session_id] = [trace_id]
                    else:
                        self.trace_dict[session_id] += [trace_id]
        self._spans.extend(spans)
        return export_lib.SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    def get_finished_spans(self, session_id: str):
        trace_ids = self.trace_dict.get(session_id, None)
        if trace_ids is None or not trace_ids:
            return []
        return [x for x in self._spans if x.context.trace_id in trace_ids]

    def clear(self):
        self._spans.clear()


class OpenSageWebServer:
    """Single-agent FastAPI server reusing provided agent and services.

    - Binds to a single app_name and prebuilt `root_agent`
    - Does not reload agent or auto-discover agents
    - Uses provided services (session/artifact/memory/credentials)
    - Exposes rich endpoints: run, SSE, live, session CRUD, artifacts, dev-UI
    """

    def __init__(
        self,
        *,
        app_name: str,
        root_agent: BaseAgent,
        fixed_session_id: str,
        session_service,
        artifact_service,
        memory_service,
        credential_service,
        eval_sets_manager=None,
        eval_set_results_manager=None,
        plugins: Optional[list[BasePlugin]] = None,
        url_prefix: Optional[str] = None,
    ):
        # Use the app_name provided by CLI (parent folder of --agent) to match ADK's expectation.
        self.app_name = app_name
        self.root_agent = root_agent
        self.fixed_session_id = fixed_session_id
        self.session_service = session_service
        self.artifact_service = artifact_service
        self.memory_service = memory_service
        self.credential_service = credential_service
        self.eval_sets_manager = eval_sets_manager
        self.eval_set_results_manager = eval_set_results_manager
        self.plugins = plugins or []
        self.url_prefix = url_prefix
        self._runner: Optional[Runner] = None

    def _build_root_session_state(
        self, base_state: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """Build canonical state for the fixed root session."""
        return build_root_session_state(
            opensage_session_id=self.fixed_session_id,
            session_id=self.fixed_session_id,
            agent_name=getattr(self.root_agent, "name", "agent"),
            base_state=base_state,
        )

    async def get_runner_async(self) -> Runner:
        if self._runner:
            return self._runner
        agentic_app = App(
            name=self.app_name, root_agent=self.root_agent, plugins=self.plugins
        )
        self._runner = Runner(
            app=agentic_app,
            artifact_service=self.artifact_service,
            session_service=self.session_service,
            memory_service=self.memory_service,
            credential_service=self.credential_service,
        )
        return self._runner

    def get_fast_api_app(
        self,
        *,
        lifespan: Optional[Lifespan[FastAPI]] = None,
        allow_origins: Optional[list[str]] = None,
        enable_dev_ui: bool = True,
    ) -> FastAPI:
        trace_memory = {}
        event_trace_index = {}
        memory_exporter = _InMemoryExporter(trace_memory)

        class _EventIdExporter(export_lib.SpanExporter):
            def __init__(self, idx):
                self.idx = idx

            def export(self, spans) -> export_lib.SpanExportResult:
                for span in spans:
                    # Collect spans relevant to request/response and tool execution.
                    if (
                        span.name == "call_llm"
                        or span.name == "send_data"
                        or span.name.startswith("execute_tool")
                    ):
                        attrs = dict(span.attributes)
                        ev_id = attrs.get("gcp.vertex.agent.event_id", None)
                        if ev_id:
                            # Store all attributes plus trace/span ids for UI consumption
                            attrs["trace_id"] = span.get_span_context().trace_id
                            attrs["span_id"] = span.get_span_context().span_id
                            self.idx[ev_id] = attrs
                return export_lib.SpanExportResult.SUCCESS

            def force_flush(self, timeout_millis: int = 30000) -> bool:
                return True

        event_exporter = _EventIdExporter(event_trace_index)
        provider = TracerProvider()
        provider.add_span_processor(export_lib.SimpleSpanProcessor(event_exporter))
        provider.add_span_processor(export_lib.SimpleSpanProcessor(memory_exporter))
        trace.set_tracer_provider(tracer_provider=provider)
        # Try to enable GenAI SDK instrumentation (optional)
        try:
            from opentelemetry.instrumentation.google_genai import (
                GoogleGenAiSdkInstrumentor,
            )

            GoogleGenAiSdkInstrumentor().instrument()
        except Exception:
            logger.warning(
                "GoogleGenAiSdkInstrumentor not available; some Request/Response traces may be missing"
            )

        app = FastAPI(lifespan=lifespan)
        active_turn_task_by_session: dict[str, asyncio.Task] = {}

        def _register_active_turn(session_id: str, task: asyncio.Task) -> None:
            # active = active_turn_task_by_session.get(session_id)
            # if active and not active.done():
            #     raise HTTPException(
            #         status_code=409,
            #         detail=(
            #             "A turn is already running for this session. Stop it first."
            #         ),
            #     )

            active_turn_task_by_session[session_id] = task

        def _clear_active_turn(session_id: str, task: asyncio.Task) -> None:
            active = active_turn_task_by_session.get(session_id)
            if active is task:
                active_turn_task_by_session.pop(session_id, None)

        if allow_origins:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=allow_origins,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )

        @app.middleware("http")
        async def _dev_ui_no_cache(request: Request, call_next):
            response = await call_next(request)
            path = request.url.path
            if path == "/dev-ui" or path.startswith("/dev-ui/"):
                # Dev UI is frequently patched while iterating; disable caching
                # Overwrite (not setdefault) to defeat previously cached immutable bundles.
                response.headers["Cache-Control"] = "no-store"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            return response

        @app.get("/list-apps")
        async def list_apps() -> list[str]:
            return [self.app_name]

        @app.get("/control/turn_state")
        async def get_turn_state(
            session_id: str = Query(default=None),
        ) -> dict[str, Any]:
            sid = session_id or self.fixed_session_id
            active = active_turn_task_by_session.get(sid)
            running = bool(active and not active.done())
            return {"running": running, "session_id": sid}

        @app.post("/control/stop_turn")
        async def stop_current_turn(
            session_id: str = Query(default=None),
        ) -> dict[str, Any]:
            sid = session_id or self.fixed_session_id
            active = active_turn_task_by_session.get(sid)
            if not active or active.done():
                return {"stopped": False, "running": False, "session_id": sid}
            active.cancel("Stopped from Dev UI")
            logger.warning("Requested stop for active turn: session_id=%s", sid)
            return {"stopped": True, "running": True, "session_id": sid}

        @app.get("/debug/trace/session/{session_id}")
        async def get_session_trace(session_id: str) -> Any:
            if session_id.startswith("subagent-"):
                session_id = session_id[len("subagent-") :]
            spans = memory_exporter.get_finished_spans(session_id)
            if not spans:
                return []
            return [
                {
                    "name": s.name,
                    "span_id": s.context.span_id,
                    "trace_id": s.context.trace_id,
                    "start_time": s.start_time,
                    "end_time": s.end_time,
                    "attributes": dict(s.attributes),
                    "parent_span_id": s.parent.span_id if s.parent else None,
                }
                for s in spans
            ]

        @app.get("/debug/trace/{event_id}")
        async def get_event_trace(event_id: str) -> Any:
            event_dict = event_trace_index.get(event_id, None)
            if event_dict is None:
                raise HTTPException(status_code=404, detail="Trace not found")
            return event_dict

        # Session APIs (single app)
        @app.get(
            "/apps/{app_name}/users/{user_id}/sessions/{session_id}",
            response_model_exclude_none=True,
        )
        async def get_session(app_name: str, user_id: str, session_id: str):
            if app_name != self.app_name:
                raise HTTPException(status_code=404, detail="App not found")
            # For sub-agent sessions, reload from traj.json to get latest events
            if session_id.startswith("subagent-"):
                child_session_id = session_id[len("subagent-") :]
                try:
                    from google.adk.sessions.session import Session as AdkSession

                    from opensage.memory.file_based.short_term.session_files import (
                        HOST_SESSION_ROOT,
                        scan_host_agent_tree,
                    )

                    tree = scan_host_agent_tree(self.fixed_session_id)
                    target = next(
                        (
                            a
                            for a in tree.get("agents_flat", [])
                            if a["session_id"] == child_session_id
                        ),
                        None,
                    )
                    if target and target["has_traj"]:
                        traj_path = (
                            HOST_SESSION_ROOT
                            / self.fixed_session_id
                            / "mem"
                            / target["dir"]
                            / "traj.json"
                        )
                        if traj_path.exists():
                            persisted = AdkSession.model_validate_json(
                                traj_path.read_text(encoding="utf-8")
                            )
                            persisted.id = session_id
                            persisted.app_name = self.app_name
                            persisted.user_id = user_id
                            # Update in-memory session
                            self.session_service.sessions.setdefault(
                                self.app_name, {}
                            ).setdefault(user_id, {})[session_id] = persisted
                except Exception as e:
                    logger.debug("Failed to reload subagent traj: %s", e)
            session = await self.session_service.get_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            return session

        @app.post(
            "/apps/{app_name}/users/{user_id}/sessions",
            response_model_exclude_none=True,
        )
        async def create_session(
            app_name: str,
            user_id: str,
            req: Optional[CreateSessionRequest] = None,
        ):
            if app_name != self.app_name:
                raise HTTPException(status_code=404, detail="App not found")
            # Always use the single fixed session id; if it exists, return it; else create.
            session = await self.session_service.get_session(
                app_name=app_name, user_id=user_id, session_id=self.fixed_session_id
            )
            if not session:
                session = await self.session_service.create_session(
                    app_name=app_name,
                    user_id=user_id,
                    state=self._build_root_session_state(req.state if req else None),
                    session_id=self.fixed_session_id,
                )
            if req and req.events:
                for event in req.events:
                    await self.session_service.append_event(
                        session=session, event=event
                    )
            return session

        @app.get(
            "/apps/{app_name}/users/{user_id}/sessions",
            response_model_exclude_none=True,
        )
        async def list_sessions(app_name: str, user_id: str):
            if app_name != self.app_name:
                raise HTTPException(status_code=404, detail="App not found")
            # Return all sessions including loaded sub-agent sessions
            # Angular uses queryParams.session to select the right one
            result = await self.session_service.list_sessions(
                app_name=app_name, user_id=user_id
            )
            sessions = result.sessions if result and result.sessions else []
            if not any(s.id == self.fixed_session_id for s in sessions):
                session = await self.session_service.create_session(
                    app_name=app_name,
                    user_id=user_id,
                    state=self._build_root_session_state(),
                    session_id=self.fixed_session_id,
                )
                sessions.append(session)
            return sessions

        @app.patch(
            "/apps/{app_name}/users/{user_id}/sessions/{session_id}/events/{event_id}",
            response_model_exclude_none=True,
        )
        async def update_session_event(
            app_name: str,
            user_id: str,
            session_id: str,
            event_id: str,
            req: UpdateSessionEventRequest,
        ) -> Event:
            if app_name != self.app_name:
                raise HTTPException(status_code=404, detail="App not found")

            session = await self.session_service.get_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            existing_event = next(
                (event for event in session.events if event.id == event_id), None
            )
            if not existing_event:
                raise HTTPException(status_code=404, detail="Event not found")

            try:
                updated_event = _replace_event_part(existing_event, req)
                return await self.session_service.update_event(
                    session=session, event=updated_event
                )
            except NotImplementedError as exc:
                raise HTTPException(status_code=501, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        @app.post("/run", response_model_exclude_none=True)
        async def run_agent(req: RunAgentRequest) -> list[Event]:
            app_name = req.app_name
            user_id = req.user_id
            session_id = req.session_id
            new_message = req.new_message
            state_delta = req.state_delta

            if app_name != self.app_name:
                raise HTTPException(status_code=404, detail="App not found")
            session = await self.session_service.get_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            runner = await self.get_runner_async()
            current_task = asyncio.current_task()
            if current_task is None:
                raise HTTPException(status_code=500, detail="No active task context")
            _register_active_turn(session_id, current_task)
            try:
                async with Aclosing(
                    runner.run_async(
                        user_id=user_id,
                        session_id=session_id,
                        new_message=new_message,
                        state_delta=state_delta,
                    )
                ) as agen:
                    return [event async for event in agen]
            except asyncio.CancelledError as cancelled:
                raise HTTPException(
                    status_code=409, detail=f"Turn stopped: {cancelled}"
                ) from cancelled
            finally:
                _clear_active_turn(session_id, current_task)

        @app.post("/run_sse")
        async def run_agent_sse(req: RunAgentRequest) -> StreamingResponse:
            app_name = req.app_name
            user_id = req.user_id
            session_id = req.session_id
            new_message = req.new_message
            streaming = bool(req.streaming)
            state_delta = req.state_delta
            invocation_id = req.invocation_id

            if app_name != self.app_name:
                raise HTTPException(status_code=404, detail="App not found")
            session = await self.session_service.get_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            async def event_generator():
                current_task = asyncio.current_task()
                if current_task is None:
                    yield 'data: {"error": "No active task context"}\n\n'
                    return
                try:
                    _register_active_turn(session_id, current_task)
                    mode = StreamingMode.SSE if streaming else StreamingMode.NONE
                    runner = await self.get_runner_async()
                    async with Aclosing(
                        runner.run_async(
                            user_id=user_id,
                            session_id=session_id,
                            new_message=new_message,
                            state_delta=state_delta,
                            run_config=RunConfig(streaming_mode=mode),
                            invocation_id=invocation_id,
                        )
                    ) as agen:
                        async for event in agen:
                            yield (
                                "data: "
                                + event.model_dump_json(
                                    exclude_none=True, by_alias=True
                                )
                                + "\n\n"
                            )
                except asyncio.CancelledError:
                    yield 'data: {"stopped": true, "message": "Turn stopped by UI"}\n\n'
                    return
                except Exception as e:
                    logger.exception("Error in SSE generator: %s", e)
                    yield f'data: {{"error": "{str(e)}"}}\n\n'
                finally:
                    _clear_active_turn(session_id, current_task)

            return StreamingResponse(event_generator(), media_type="text/event-stream")

        @app.websocket("/run_live")
        async def run_agent_live(
            websocket: WebSocket,
            app_name: str,
            user_id: str,
            session_id: str,
            modalities: List[Literal["TEXT", "AUDIO"]] = Query(
                default=["TEXT", "AUDIO"]
            ),
        ):
            await websocket.accept()
            if app_name != self.app_name:
                await websocket.close(code=1002, reason="App not found")
                return
            session = await self.session_service.get_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )
            if not session:
                await websocket.close(code=1002, reason="Session not found")
                return

            live_request_queue = LiveRequestQueue()

            async def forward_events():
                runner = await self.get_runner_async()
                async with Aclosing(
                    runner.run_live(
                        session=session, live_request_queue=live_request_queue
                    )
                ) as agen:
                    async for event in agen:
                        await websocket.send_text(
                            event.model_dump_json(exclude_none=True, by_alias=True)
                        )

            async def process_messages():
                try:
                    while True:
                        data = await websocket.receive_text()
                        live_request_queue.send(LiveRequest.model_validate_json(data))
                except ValidationError as ve:
                    logger.exception(
                        "Validation error in live process_messages: %s", ve
                    )

            tasks = [
                asyncio.create_task(forward_events()),
                asyncio.create_task(process_messages()),
            ]
            current_task = asyncio.current_task()
            if current_task is None:
                await websocket.close(code=1011, reason="No active task context")
                return
            _register_active_turn(session_id, current_task)
            try:
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_EXCEPTION
                )
                for t in done:
                    t.result()
            except asyncio.CancelledError:
                await websocket.close(code=1013, reason="Turn stopped by UI")
            except WebSocketDisconnect:
                logger.info("Client disconnected")
            except Exception as e:
                logger.exception("Live error: %s", e)
                await websocket.close(code=1011, reason=str(e)[:123])
            finally:
                _clear_active_turn(session_id, current_task)
                for t in tasks:
                    t.cancel()

        # Artifacts
        @app.get(
            "/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts/{artifact_name}",
            response_model_exclude_none=True,
        )
        async def load_artifact(
            app_name: str,
            user_id: str,
            session_id: str,
            artifact_name: str,
            version: Optional[int] = Query(None),
        ) -> Optional[types.Part]:
            if app_name != self.app_name:
                raise HTTPException(status_code=404, detail="App not found")
            artifact = await self.artifact_service.load_artifact(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                filename=artifact_name,
                version=version,
            )
            if not artifact:
                raise HTTPException(status_code=404, detail="Artifact not found")
            return artifact

        @app.get(
            "/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts/{artifact_name}/versions/{version_id}",
            response_model_exclude_none=True,
        )
        async def load_artifact_version(
            app_name: str,
            user_id: str,
            session_id: str,
            artifact_name: str,
            version_id: int,
        ) -> Optional[types.Part]:
            if app_name != self.app_name:
                raise HTTPException(status_code=404, detail="App not found")
            artifact = await self.artifact_service.load_artifact(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                filename=artifact_name,
                version=version_id,
            )
            if not artifact:
                raise HTTPException(status_code=404, detail="Artifact not found")
            return artifact

        @app.get(
            "/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts",
            response_model_exclude_none=True,
        )
        async def list_artifacts(
            app_name: str, user_id: str, session_id: str
        ) -> list[str]:
            if app_name != self.app_name:
                raise HTTPException(status_code=404, detail="App not found")
            return await self.artifact_service.list_artifact_keys(
                app_name=app_name, user_id=user_id, session_id=session_id
            )

        # Minimal builder endpoints for Dev UI compatibility
        @app.get(
            "/builder/app/{app_name}",
            response_model_exclude_none=True,
            response_class=PlainTextResponse,
        )
        async def get_agent_builder(
            app_name: str, file_path: Optional[str] = None, tmp: Optional[bool] = False
        ):
            # Serve root_agent.yaml if exists, otherwise empty
            base_path = Path.cwd()
            agent_dir = base_path / app_name
            if tmp:
                agent_dir = agent_dir / "tmp" / app_name
            if not file_path:
                file_name = "root_agent.yaml"
                root_file_path = agent_dir / file_name
                if not root_file_path.is_file():
                    return ""
                else:
                    return FileResponse(
                        path=root_file_path,
                        media_type="application/x-yaml",
                        filename="${app_name}.yaml",
                        headers={"Cache-Control": "no-store"},
                    )
            else:
                agent_file_path = agent_dir / file_path
                if not agent_file_path.is_file():
                    return ""
                else:
                    return FileResponse(
                        path=agent_file_path,
                        media_type="application/x-yaml",
                        filename=file_path,
                        headers={"Cache-Control": "no-store"},
                    )

        if enable_dev_ui:
            # Serve vendored Dev UI assets (copied into this repo and offline patched).
            web_assets_dir = Path(__file__).parent / "vendor" / "adk_browser"
            if not web_assets_dir.exists():
                raise FileNotFoundError(
                    "Vendored Dev UI assets not found. Expected directory: "
                    f"{web_assets_dir}."
                )
            import mimetypes

            mimetypes.add_type("application/javascript", ".js", True)
            mimetypes.add_type("text/javascript", ".js", True)
            dev_ui_base_url = (
                self.url_prefix + "/dev-ui/" if self.url_prefix else "/dev-ui/"
            )
            redirect_dev_ui_url = f"{dev_ui_base_url}?" + urlencode(
                {
                    "app": self.app_name,
                    "session": self.fixed_session_id,
                    "userId": "user",
                }
            )

            @app.get("/dev-ui/config")
            async def get_ui_config():
                return {
                    "logo_text": "OpenSage",
                    # Served from vendored static assets (offline replaced).
                    "logo_image_url": "assets/opensage.svg",
                }

            # --- Sub-agent visualization endpoints ---

            @app.get("/control/subagents")
            async def list_subagents():
                from opensage.memory.file_based.short_term.session_files import (
                    scan_host_agent_tree,
                )

                tree = scan_host_agent_tree(self.fixed_session_id)
                root = tree.get("root")
                root_name = root["name"] if root else ""
                subagents = [
                    a for a in tree.get("agents_flat", []) if a["name"] != root_name
                ]
                return {"agents": subagents, "root": root_name}

            @app.get("/control/subagents/topology")
            async def get_subagent_topology():
                from opensage.memory.file_based.short_term.session_files import (
                    scan_host_agent_tree,
                )

                tree = scan_host_agent_tree(self.fixed_session_id)
                root = tree.get("root")
                if not root:
                    return {"nodes": [], "edges": []}

                nodes: list[dict] = []
                edges: list[dict] = []

                def _walk(node, parent_id=None):
                    nid = node.get("session_id") or node["name"]
                    is_root = parent_id is None
                    query = node.get("query", "")
                    # Build label: name + precise session id + query preview.
                    label = node["name"]
                    if node.get("session_id"):
                        label += f"\n[{node['session_id']}]"
                    if query and not is_root:
                        preview = query[:40].replace("\n", " ")
                        if len(query) > 40:
                            preview += "..."
                        label += f"\n({preview})"
                    nodes.append(
                        {
                            "id": nid,
                            "label": label,
                            "name": node["name"],
                            "session_id": node.get("session_id", ""),
                            "query": query,
                            "status": (
                                "active" if is_root else node.get("status", "running")
                            ),
                            "type": "root" if is_root else "subagent",
                        }
                    )
                    if parent_id is not None:
                        edges.append({"from": parent_id, "to": nid})
                    for child in node.get("children", []):
                        _walk(child, parent_id=nid)

                _walk(root)
                return {"nodes": nodes, "edges": edges}

            @app.get("/control/subagents/{subagent_session_id}/events")
            async def get_subagent_events(
                subagent_session_id: str,
                after_index: int = Query(default=0),
            ):
                from google.adk.sessions.session import Session

                from opensage.memory.file_based.short_term.session_files import (
                    HOST_SESSION_ROOT,
                    scan_host_agent_tree,
                )

                tree = scan_host_agent_tree(self.fixed_session_id)
                target = next(
                    (
                        a
                        for a in tree.get("agents_flat", [])
                        if a["session_id"] == subagent_session_id
                    ),
                    None,
                )
                if not target or not target["has_traj"]:
                    return {
                        "agent_name": "",
                        "subagent_session_id": subagent_session_id,
                        "events": [],
                        "total": 0,
                    }
                traj_path = (
                    HOST_SESSION_ROOT
                    / self.fixed_session_id
                    / "mem"
                    / target["dir"]
                    / "traj.json"
                )
                if not traj_path.exists():
                    return {
                        "agent_name": "",
                        "subagent_session_id": subagent_session_id,
                        "events": [],
                        "total": 0,
                    }
                try:
                    child_session = Session.model_validate_json(
                        traj_path.read_text(encoding="utf-8")
                    )
                except Exception:
                    return {
                        "agent_name": "",
                        "subagent_session_id": subagent_session_id,
                        "events": [],
                        "total": 0,
                    }

                events = child_session.events or []
                filtered = []
                for i, event in enumerate(events):
                    if i < after_index:
                        continue
                    try:
                        filtered.append(
                            {
                                "index": i,
                                "id": event.id,
                                "author": event.author,
                                "timestamp": event.timestamp,
                                "content": event.model_dump(
                                    exclude_none=True, by_alias=True
                                ),
                            }
                        )
                    except Exception:
                        continue
                return {
                    "agent_name": target["name"],
                    "subagent_session_id": subagent_session_id,
                    "events": filtered,
                    "total": len(events),
                }

            @app.post("/control/subagents/{subagent_session_id}/load_session")
            async def load_subagent_session(subagent_session_id: str):
                """Load a sub-agent's traj.json into session_service so Angular can render it natively."""
                from google.adk.sessions.session import Session

                from opensage.memory.file_based.short_term.session_files import (
                    HOST_SESSION_ROOT,
                    scan_host_agent_tree,
                )

                tree = scan_host_agent_tree(self.fixed_session_id)
                target = next(
                    (
                        a
                        for a in tree.get("agents_flat", [])
                        if a["session_id"] == subagent_session_id
                    ),
                    None,
                )
                if not target or not target["has_traj"]:
                    raise HTTPException(
                        status_code=404,
                        detail=(
                            "No traj.json found for subagent session "
                            f"'{subagent_session_id}'"
                        ),
                    )
                traj_path = (
                    HOST_SESSION_ROOT
                    / self.fixed_session_id
                    / "mem"
                    / target["dir"]
                    / "traj.json"
                )
                if not traj_path.exists():
                    raise HTTPException(status_code=404, detail="traj.json not found")
                persisted = Session.model_validate_json(
                    traj_path.read_text(encoding="utf-8")
                )
                # Use the real child session id so same-name instances stay distinct.
                sub_session_id = f"subagent-{subagent_session_id}"
                persisted.id = sub_session_id
                persisted.app_name = self.app_name
                persisted.user_id = "user"
                # Check if already loaded
                existing = await self.session_service.get_session(
                    app_name=self.app_name,
                    user_id="user",
                    session_id=sub_session_id,
                )
                if existing:
                    # Update events in place
                    existing.events = persisted.events
                else:
                    await self.session_service.create_session(
                        app_name=self.app_name,
                        user_id="user",
                        state=persisted.state or {},
                        session_id=sub_session_id,
                    )
                    # Inject the full session with events
                    self.session_service.sessions.setdefault(
                        self.app_name, {}
                    ).setdefault("user", {})[sub_session_id] = persisted
                return {
                    "session_id": sub_session_id,
                    "agent_name": target["name"],
                    "subagent_session_id": subagent_session_id,
                    "event_count": len(persisted.events or []),
                }

            @app.post("/control/upload_to_sandbox")
            async def upload_file_to_sandbox(
                file: UploadFile = File(...),
                sandbox_type: str = Form("main"),
                target_path: str | None = Form(None),
            ) -> dict[str, Any]:
                if not file.filename:
                    raise HTTPException(status_code=400, detail="File is required")

                from opensage.session import get_opensage_session

                opensage_session = get_opensage_session(self.fixed_session_id)
                available_sandboxes = opensage_session.sandboxes.list_sandboxes()
                sandbox = available_sandboxes.get(sandbox_type)
                if sandbox is None:
                    raise HTTPException(
                        status_code=404,
                        detail=(
                            f"Sandbox '{sandbox_type}' not found. Available: "
                            f"{', '.join(sorted(available_sandboxes))}"
                        ),
                    )

                filename = Path(file.filename).name
                resolved_target_path = (
                    target_path.strip()
                    if target_path and target_path.strip()
                    else f"/shared/uploads/{filename}"
                )
                if resolved_target_path.endswith("/"):
                    resolved_target_path = f"{resolved_target_path}{filename}"
                if not resolved_target_path.startswith("/"):
                    raise HTTPException(
                        status_code=400,
                        detail="target_path must be an absolute sandbox path",
                    )

                parent_dir = str(Path(resolved_target_path).parent)
                temp_path = None
                try:
                    suffix = Path(filename).suffix
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=suffix
                    ) as temp_file:
                        while chunk := await file.read(1024 * 1024):
                            temp_file.write(chunk)
                        temp_path = temp_file.name

                    _, mkdir_exit_code = await sandbox.arun_command_in_container(
                        ["mkdir", "-p", parent_dir]
                    )
                    if mkdir_exit_code != 0:
                        raise HTTPException(
                            status_code=500,
                            detail=f"Failed to create sandbox directory: {parent_dir}",
                        )

                    await sandbox.acopy_file_to_container(
                        temp_path, resolved_target_path
                    )
                except HTTPException:
                    raise
                except Exception as exc:
                    logger.exception(
                        "Failed to upload file to sandbox %s:%s",
                        sandbox_type,
                        resolved_target_path,
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=f"Upload failed: {exc}",
                    ) from exc
                finally:
                    await file.close()
                    if temp_path and os.path.exists(temp_path):
                        os.unlink(temp_path)

                return {
                    "ok": True,
                    "sandbox_type": sandbox_type,
                    "target_path": resolved_target_path,
                    "filename": filename,
                }

            @app.get("/")
            async def redirect_root_to_dev_ui():
                return RedirectResponse(redirect_dev_ui_url)

            @app.get("/dev-ui")
            async def redirect_dev_ui_add_slash():
                return RedirectResponse(redirect_dev_ui_url)

            app.mount(
                "/dev-ui/",
                StaticFiles(directory=web_assets_dir, html=True, follow_symlink=True),
                name="static",
            )

        # Compatibility endpoints returning empty lists for Dev UI expectations
        @app.get("/apps/{app_name}/eval_results", response_model_exclude_none=True)
        async def list_eval_results_legacy(app_name: str) -> list[str]:
            if app_name != self.app_name:
                raise HTTPException(status_code=404, detail="App not found")
            return []

        @app.get("/apps/{app_name}/eval_sets", response_model_exclude_none=True)
        async def list_eval_sets_legacy(app_name: str) -> list[str]:
            if app_name != self.app_name:
                raise HTTPException(status_code=404, detail="App not found")
            return []

        # Event graph endpoint (align with ADK)
        @app.get(
            "/apps/{app_name}/users/{user_id}/sessions/{session_id}/events/{event_id}/graph",
            response_model_exclude_none=True,
        )
        async def get_event_graph(
            app_name: str, user_id: str, session_id: str, event_id: str
        ):
            if app_name != self.app_name:
                raise HTTPException(status_code=404, detail="App not found")
            session = await self.session_service.get_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )
            session_events = session.events if session else []
            event = next((x for x in session_events if x.id == event_id), None)
            if not event:
                return {}

            # Build highlight edges from function calls/responses
            function_calls = event.get_function_calls()
            function_responses = event.get_function_responses()
            dot_graph = None
            root_agent = self.root_agent
            if function_calls:
                highlights = []
                for fc in function_calls:
                    from_name = event.author
                    to_name = fc.name
                    highlights.append((from_name, to_name))
                    dot_graph = await agent_graph.get_agent_graph(
                        root_agent, highlights
                    )
            elif function_responses:
                highlights = []
                for fr in function_responses:
                    from_name = fr.name
                    to_name = event.author
                    highlights.append((from_name, to_name))
                    dot_graph = await agent_graph.get_agent_graph(
                        root_agent, highlights
                    )
            else:
                from_name = event.author
                to_name = ""
                dot_graph = await agent_graph.get_agent_graph(
                    root_agent, [(from_name, to_name)]
                )

            if dot_graph and isinstance(dot_graph, graphviz.Digraph):
                return GetEventGraphResult(dot_src=dot_graph.source)
            return GetEventGraphResult(dot_src="")

        return app
