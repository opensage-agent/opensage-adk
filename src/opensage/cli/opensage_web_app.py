from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, List, Literal, Optional

import graphviz
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
    HTMLResponse,
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
                agent_name = session_id[len("subagent-") :]
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
                            if a["name"] == agent_name
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
                    logger.error("Validation error in live process_messages: %s", ve)

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
            redirect_dev_ui_url = (
                self.url_prefix + "/dev-ui/" if self.url_prefix else "/dev-ui/"
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
                counter = [0]

                def _walk(node, parent_id=None):
                    nid = f"n{counter[0]}"
                    counter[0] += 1
                    is_root = parent_id is None
                    query = node.get("query", "")
                    # Build label: name + query preview
                    label = node["name"]
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
                            "query": query,
                            "status": (
                                "active"
                                if is_root
                                else ("completed" if node["has_traj"] else "running")
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

            @app.get("/control/subagents/{agent_name}/events")
            async def get_subagent_events(
                agent_name: str,
                after_index: int = Query(default=0),
            ):
                from google.adk.sessions.session import Session

                from opensage.memory.file_based.short_term.session_files import (
                    HOST_SESSION_ROOT,
                    scan_host_agent_tree,
                )

                tree = scan_host_agent_tree(self.fixed_session_id)
                target = next(
                    (a for a in tree.get("agents_flat", []) if a["name"] == agent_name),
                    None,
                )
                if not target or not target["has_traj"]:
                    return {
                        "agent_name": agent_name,
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
                        "agent_name": agent_name,
                        "events": [],
                        "total": 0,
                    }
                try:
                    child_session = Session.model_validate_json(
                        traj_path.read_text(encoding="utf-8")
                    )
                except Exception:
                    return {
                        "agent_name": agent_name,
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
                    "agent_name": agent_name,
                    "events": filtered,
                    "total": len(events),
                }

            @app.post("/control/subagents/{agent_name}/load_session")
            async def load_subagent_session(agent_name: str):
                """Load a sub-agent's traj.json into session_service so Angular can render it natively."""
                from google.adk.sessions.session import Session

                from opensage.memory.file_based.short_term.session_files import (
                    HOST_SESSION_ROOT,
                    scan_host_agent_tree,
                )

                tree = scan_host_agent_tree(self.fixed_session_id)
                target = next(
                    (a for a in tree.get("agents_flat", []) if a["name"] == agent_name),
                    None,
                )
                if not target or not target["has_traj"]:
                    raise HTTPException(
                        status_code=404,
                        detail=f"No traj.json found for agent '{agent_name}'",
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
                # Use a deterministic session ID so reloading reuses the same session
                sub_session_id = f"subagent-{agent_name}"
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
                    "agent_name": agent_name,
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

                    _, mkdir_exit_code = sandbox.run_command_in_container(
                        ["mkdir", "-p", parent_dir]
                    )
                    if mkdir_exit_code != 0:
                        raise HTTPException(
                            status_code=500,
                            detail=f"Failed to create sandbox directory: {parent_dir}",
                        )

                    sandbox.copy_file_to_container(temp_path, resolved_target_path)
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

            @app.get("/dev-ui/opensage-stop-turn.js")
            async def get_stop_turn_js():
                js = """
(() => {
  if (window !== window.top) return;
  const style = document.createElement('style');
  style.textContent = `
    #opensage-stop-btn {
      position: fixed; right: 16px; bottom: 16px; z-index: 99999;
      border: none; border-radius: 8px; padding: 10px 14px;
      color: #fff; font-weight: 600; cursor: pointer;
      box-shadow: 0 2px 8px rgba(0,0,0,.25);
    }
    #opensage-stop-btn.running { background: #d93025; }
    #opensage-stop-btn.idle { background: #9aa0a6; cursor: not-allowed; }
  `;
  document.head.appendChild(style);

  const btn = document.createElement('button');
  btn.id = 'opensage-stop-btn';
  btn.className = 'idle';
  btn.textContent = 'Stop Turn';
  btn.disabled = true;
  document.body.appendChild(btn);

  async function refresh() {
    try {
      const res = await fetch('/control/turn_state', { cache: 'no-store' });
      const data = await res.json();
      const running = !!data.running;
      btn.className = running ? 'running' : 'idle';
      btn.disabled = !running;
    } catch (_) {}
  }

  btn.addEventListener('click', async () => {
    try {
      await fetch('/control/stop_turn', { method: 'POST' });
      await refresh();
    } catch (_) {}
  });

  refresh();
  setInterval(refresh, 3000);
})();
                """.strip()
                return PlainTextResponse(js, media_type="application/javascript")

            @app.get("/dev-ui/opensage-upload-sandbox.js")
            async def get_upload_sandbox_js():
                js = """
(() => {
  if (window !== window.top) return;
  const style = document.createElement('style');
  style.textContent = `
    #opensage-upload-btn {
      position: fixed; right: 16px; bottom: 68px; z-index: 99999;
      border: none; border-radius: 8px; padding: 10px 14px;
      color: #fff; font-weight: 600; cursor: pointer;
      background: #1a73e8; box-shadow: 0 2px 8px rgba(0,0,0,.25);
    }
    #opensage-upload-btn:disabled {
      background: #9aa0a6; cursor: progress;
    }
  `;
  document.head.appendChild(style);

  const input = document.createElement('input');
  input.type = 'file';
  input.style.display = 'none';
  document.body.appendChild(input);

  const btn = document.createElement('button');
  btn.id = 'opensage-upload-btn';
  btn.textContent = 'Upload to Sandbox';
  btn.title = 'Upload a local file into the current sandbox';
  document.body.appendChild(btn);

  async function uploadFile(file) {
    const sandboxType = window.prompt('Upload to which sandbox?', 'main');
    if (sandboxType === null) return;

    const defaultPath = `/shared/uploads/${file.name}`;
    const targetPath = window.prompt(
      'Upload to which sandbox path?',
      defaultPath
    );
    if (targetPath === null) return;

    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = 'Uploading...';

    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('sandbox_type', sandboxType.trim() || 'main');
      formData.append('target_path', targetPath.trim() || defaultPath);

      const response = await fetch('/control/upload_to_sandbox', {
        method: 'POST',
        body: formData,
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || 'Upload failed');
      }
      alert(`Uploaded to ${data.target_path}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      alert(`Upload failed: ${message}`);
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
      input.value = '';
    }
  }

  btn.addEventListener('click', () => input.click());
  input.addEventListener('change', () => {
    const [file] = input.files || [];
    if (file) {
      uploadFile(file);
    }
  });
})();
                """.strip()
                return PlainTextResponse(js, media_type="application/javascript")

            @app.get("/dev-ui/opensage-subagent-panel.js")
            async def get_subagent_panel_js():
                js = r"""
(() => {
  /* Do not run inside iframes (sub-agent session tabs) */
  if (window !== window.top) return;

  const S = document.createElement('style');
  S.textContent = `
    #osp-tabbar {
      position: fixed; top: 0; left: 0; right: 0; z-index: 99990;
      display: flex; align-items: center; gap: 0;
      background: var(--mat-sys-surface-container, #2b2b2b);
      border-bottom: 1px solid var(--mat-sys-outline-variant, #444);
      font-family: Google Sans, Helvetica Neue, sans-serif;
      font-size: 13px; height: 38px; padding: 0 8px;
      box-shadow: 0 1px 4px rgba(0,0,0,.3);
    }
    .osp-tab {
      padding: 8px 16px; border: none; background: none;
      color: var(--mat-sys-on-surface-variant, #9d9d9d);
      cursor: pointer; font-family: inherit; font-size: 13px; font-weight: 500;
      position: relative; white-space: nowrap; transition: color .15s;
      display: flex; align-items: center; gap: 6px;
    }
    .osp-tab:hover { color: var(--mat-sys-on-surface, #e3e3e3); }
    .osp-tab.active { color: var(--mat-sys-primary, #8ab4f8); }
    .osp-tab.active::after {
      content: ''; position: absolute; bottom: 0; left: 8px; right: 8px;
      height: 3px; border-radius: 3px 3px 0 0;
      background: var(--mat-sys-primary, #8ab4f8);
    }
    .osp-tab-close {
      display: inline-flex; align-items: center; justify-content: center;
      width: 16px; height: 16px; border-radius: 50%; border: none; background: none;
      color: var(--mat-sys-on-surface-variant, #656565); cursor: pointer;
      font-size: 14px; line-height: 1; padding: 0;
    }
    .osp-tab-close:hover { background: var(--mat-sys-surface-container, #444); color: #fff; }
    .osp-tab-badge {
      display: inline-block; padding: 1px 6px; border-radius: 8px;
      font-size: 10px; font-weight: 600; margin-left: 4px;
    }
    .osp-tab-badge-running { background: #81c99533; color: #81c995; }
    .osp-tab-badge-completed { background: #8ab4f822; color: #8ab4f8; }
    #osp-content-wrapper {
      position: fixed; top: 38px; left: 0; right: 0; bottom: 0;
    }
    .osp-frame {
      position: absolute; top: 0; left: 0; width: 100%; height: 100%;
      border: none;
    }
    #osp-topo-pane {
      position: absolute; top: 0; left: 0; width: 100%; height: 100%;
      background: var(--mat-sys-surface, #1e1e1e);
      display: none;
    }
    .osp-empty {
      color: var(--mat-sys-on-surface-variant, #656565); text-align: center;
      padding: 80px 24px; font-size: 14px;
      font-family: Google Sans, Helvetica Neue, sans-serif;
    }
  `;
  document.head.appendChild(S);

  /* ---- State ---- */
  const baseUrl = window.location.href.split('?')[0];
  /* Read URL params lazily since Angular may set them after page load */
  function _getParams() {
    const p = new URLSearchParams(window.location.search);
    return {
      app: p.get('app') || '',
      session: p.get('session') || '',
      userId: p.get('userId') || 'user',
    };
  }
  /* Also fetch app name from API as fallback */
  let _cachedAppName = '';
  fetch('/list-apps', {cache:'no-store'}).then(r=>r.json()).then(d=>{
    if (Array.isArray(d) && d.length > 0) _cachedAppName = d[0];
  }).catch(()=>{});
  function getAppName() { return _getParams().app || _cachedAppName; }
  function getUserId() { return _getParams().userId; }

  /* tabs: [{id, label, type:'main'|'topology'|'subagent', sessionId?, agentName?, status?}] */
  const tabs = [
    { id: 'main', label: 'Session', type: 'main' },
    { id: 'topology', label: 'Topology', type: 'topology' },
    { id: 'list', label: 'Sub-Agents', type: 'list' },
  ];
  let activeTabId = 'main';

  /* ---- Push existing page content into an iframe-like wrapper ---- */
  /* We move the existing body content into the main pane, and overlay topology + subagent iframes */
  document.body.style.margin = '0';
  document.body.style.overflow = 'hidden';

  /* Tab bar */
  const tabbar = document.createElement('div');
  tabbar.id = 'osp-tabbar';
  document.body.prepend(tabbar);

  /* Content wrapper */
  const wrapper = document.createElement('div');
  wrapper.id = 'osp-content-wrapper';
  document.body.appendChild(wrapper);

  /* Move existing app-root into the wrapper as-is, in a container div */
  const mainPane = document.createElement('div');
  mainPane.id = 'osp-main-pane';
  mainPane.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;overflow:auto';
  const appRoot = document.querySelector('app-root') || document.querySelector('body > *:not(#osp-tabbar):not(#osp-content-wrapper):not(style):not(script)');
  if (appRoot) {
    mainPane.appendChild(appRoot);
  }
  wrapper.appendChild(mainPane);

  /* Topology pane */
  const topoPane = document.createElement('div');
  topoPane.id = 'osp-topo-pane';
  topoPane.innerHTML = '<div class="osp-empty">Loading topology...</div>';
  wrapper.appendChild(topoPane);

  /* List pane */
  const listPane = document.createElement('div');
  listPane.id = 'osp-list-pane';
  listPane.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;background:var(--mat-sys-surface,#1e1e1e);display:none;overflow:auto;font-family:Google Sans,Helvetica Neue,sans-serif;font-size:13px;color:var(--mat-sys-on-surface,#e3e3e3)';
  listPane.innerHTML = '<div class="osp-empty">Loading...</div>';
  wrapper.appendChild(listPane);

  /* ---- Tab rendering ---- */
  function renderTabs() {
    tabbar.innerHTML = '';
    tabs.forEach(t => {
      const btn = document.createElement('button');
      btn.className = 'osp-tab' + (t.id === activeTabId ? ' active' : '');
      let label = t.label;
      if (t.type === 'subagent' && t.status) {
        const cls = t.status === 'running' ? 'running' : 'completed';
        label += `<span class="osp-tab-badge osp-tab-badge-${cls}">${t.status}</span>`;
      }
      btn.innerHTML = label;
      btn.addEventListener('click', () => activateTab(t.id));

      if (t.type === 'subagent') {
        const closeBtn = document.createElement('button');
        closeBtn.className = 'osp-tab-close';
        closeBtn.innerHTML = '\u00d7';
        closeBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          closeTab(t.id);
        });
        btn.appendChild(closeBtn);
      }
      tabbar.appendChild(btn);
    });
  }

  function activateTab(id) {
    activeTabId = id;
    renderTabs();
    /* Show/hide panes */
    mainPane.style.display = id === 'main' ? 'block' : 'none';
    topoPane.style.display = id === 'topology' ? 'block' : 'none';
    listPane.style.display = id === 'list' ? 'block' : 'none';
    /* Sub-agent iframes */
    wrapper.querySelectorAll('.osp-subagent-frame').forEach(f => {
      f.style.display = f.dataset.tabId === id ? 'block' : 'none';
    });
    if (id === 'topology') refreshTopology();
    if (id === 'list') refreshList();
  }

  function closeTab(id) {
    const idx = tabs.findIndex(t => t.id === id);
    if (idx < 0) return;
    tabs.splice(idx, 1);
    /* Remove iframe */
    const frame = wrapper.querySelector(`[data-tab-id="${id}"]`);
    if (frame) frame.remove();
    /* Switch to topology if closing active tab */
    if (activeTabId === id) activateTab('topology');
    else renderTabs();
  }

  function openSubagentTab(agentName, status) {
    const tabId = 'sub-' + agentName;
    /* If tab already exists, refresh it and activate */
    if (tabs.find(t => t.id === tabId)) {
      refreshSubagentTab(tabId);
      activateTab(tabId);
      return;
    }
    /* Load session into backend, then create iframe */
    fetch(`/control/subagents/${encodeURIComponent(agentName)}/load_session`, { method: 'POST' })
      .then(r => {
        if (!r.ok) return r.text().then(t => { throw new Error(`${r.status}: ${t}`); });
        return r.json();
      })
      .then(data => {
        console.log('[SubAgent] Loaded session:', data);
        const sessionId = data.session_id;
        tabs.push({
          id: tabId, label: agentName, type: 'subagent',
          sessionId, agentName, status: status || 'completed',
        });
        /* Create iframe pointing to Angular app with sub-agent session */
        const iframe = document.createElement('iframe');
        iframe.className = 'osp-frame osp-subagent-frame';
        iframe.dataset.tabId = tabId;
        const app = getAppName();
        const uid = getUserId();
        const iframeSrc = `${baseUrl}?app=${encodeURIComponent(app)}&session=${encodeURIComponent(sessionId)}&userId=${encodeURIComponent(uid)}`;
        console.log('[SubAgent] iframe src:', iframeSrc, 'appName:', app, 'sessionId:', sessionId);
        iframe.src = iframeSrc;
        iframe.style.display = 'none';
        wrapper.appendChild(iframe);
        activateTab(tabId);
      })
      .catch(err => {
        console.error('[SubAgent] Failed to load session:', err);
        alert('Failed to load sub-agent session: ' + err.message);
      });
  }

  /* ---- Topology ---- */
  let net = null;
  let _lastTopoHash = '';
  async function refreshTopology() {
    try {
      const r = await fetch('/control/subagents/topology', {cache:'no-store'});
      const d = await r.json();
      if (!d.nodes || d.nodes.length <= 1) {
        if (_lastTopoHash !== 'empty') {
          topoPane.innerHTML = '<div class="osp-empty">No sub-agents spawned yet</div>';
          if (net) { net.destroy(); net = null; }
          _lastTopoHash = 'empty';
        }
        return;
      }
      /* Only rebuild if data changed */
      const newHash = JSON.stringify(d.nodes.map(n=>n.id+n.status)) + JSON.stringify(d.edges);
      if (newHash === _lastTopoHash && net) return;
      _lastTopoHash = newHash;

      const cm = { active:'#8ab4f8', running:'#81c995', completed:'#5f9dff', error:'#f28b82' };
      if (typeof vis !== 'undefined') {
        const vn = d.nodes.map(n => ({
          id: n.id, label: n.label,
          color: { background: cm[n.status]||'#656565', border: 'transparent',
                   highlight: {background:'#ffe665', border:'#ffe665'} },
          font: { color: '#fff', size: 14, face: 'Google Sans, sans-serif' },
          shape: n.type === 'root' ? 'diamond' : 'box',
          borderWidth: 0, borderWidthSelected: 2,
          margin: { top: 10, bottom: 10, left: 14, right: 14 },
        }));
        const ve = d.edges.map(e => ({
          from: e.from, to: e.to, arrows: {to:{enabled:true,scaleFactor:.6}},
          color: {color:'#4d4d4d', highlight:'#8ab4f8'}, width: 2,
          smooth: {type:'cubicBezier', roundness:.4},
        }));
        if (net) net.destroy();
        topoPane.innerHTML = '';
        net = new vis.Network(topoPane, {nodes:vn, edges:ve}, {
          layout: {hierarchical:{direction:'UD', sortMethod:'directed', levelSeparation:80, nodeSpacing:120}},
          physics: false,
          interaction: {hover:true, zoomView:true, dragView:true},
        });
        net.on('click', p => {
          if (p.nodes.length === 1) {
            const nd = d.nodes.find(n => n.id === p.nodes[0]);
            if (nd && nd.type !== 'root') openSubagentTab(nd.name, nd.status);
          }
        });
      } else {
        let h = '<div style="padding:24px;font-family:monospace;font-size:13px;white-space:pre;color:#b2b2b2">';
        d.nodes.forEach(n => {
          const pre = n.type === 'root' ? '' : '  \u2514\u2500 ';
          const dot = n.status === 'running' ? '\u25cf' : n.status === 'completed' ? '\u25c6' : '\u25cb';
          const sc = cm[n.status] || '#656565';
          h += `${pre}<span style="color:${sc};cursor:${n.type==='root'?'default':'pointer'}" onclick="window.__ospOpenSub && window.__ospOpenSub('${n.label}')">${dot} ${n.label}</span>\n`;
        });
        h += '</div>';
        topoPane.innerHTML = h;
        window.__ospOpenSub = (name) => openSubagentTab(name);
      }
    } catch(_) {
      topoPane.innerHTML = '<div class="osp-empty">Failed to load topology</div>';
    }
  }

  /* ---- List ---- */
  async function refreshList() {
    try {
      const r = await fetch('/control/subagents', {cache:'no-store'});
      const d = await r.json();
      const cnt = d.agents.length;
      /* Update Sub-Agents tab label with count */
      const listTab = tabs.find(t => t.id === 'list');
      if (listTab) listTab.label = cnt > 0 ? `Sub-Agents (${cnt})` : 'Sub-Agents';
      renderTabs();
      if (!cnt) { listPane.innerHTML = '<div class="osp-empty">No sub-agents created yet</div>'; return; }
      listPane.innerHTML = '<div style="padding:8px 0">' + d.agents.map(a => {
        const badge = a.status === 'running'
          ? '<span style="display:inline-block;padding:2px 10px;border-radius:12px;font-size:11px;background:#81c99533;color:#81c995;border:1px solid #81c99544">running</span>'
          : '<span style="display:inline-block;padding:2px 10px;border-radius:12px;font-size:11px;background:#8ab4f822;color:#8ab4f8;border:1px solid #8ab4f844">completed</span>';
        const query = a.query ? `<div style="color:var(--mat-sys-on-surface-variant,#9d9d9d);font-size:12px;margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_esc(a.query)}</div>` : '';
        return `<div data-n="${_esc(a.name)}" style="padding:12px 20px;border-bottom:1px solid var(--mat-sys-outline-variant,#333);cursor:pointer;transition:background .1s" onmouseover="this.style.background='var(--mat-sys-surface-container,#2b2b2b)'" onmouseout="this.style.background=''">
          <div style="display:flex;align-items:center;gap:8px">
            <strong>${_esc(a.name)}</strong> ${badge}
          </div>
          <div style="color:var(--mat-sys-on-surface-variant,#656565);font-size:12px;margin-top:2px">${a.parent_name ? 'called by ' + _esc(a.parent_name) : 'root'}</div>
          ${query}
        </div>`;
      }).join('') + '</div>';
      listPane.querySelectorAll('[data-n]').forEach(el => {
        el.addEventListener('click', () => openSubagentTab(el.dataset.n));
      });
    } catch(_) {}
  }

  function _esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

  /* ---- Init ---- */
  renderTabs();
  activateTab('main');

  /* Auto-refresh topology when visible */
  setInterval(() => {
    if (activeTabId === 'topology') refreshTopology();
    if (activeTabId === 'list') refreshList();
  }, 5000);

  /* Refresh sub-agent tab on click: reload session from traj.json then reload iframe */
  function refreshSubagentTab(tabId) {
    const tab = tabs.find(t => t.id === tabId);
    if (!tab || !tab.agentName) return;
    const frame = wrapper.querySelector(`[data-tab-id="${tabId}"]`);
    if (!frame) return;
    /* Re-load session from latest traj.json */
    fetch(`/control/subagents/${encodeURIComponent(tab.agentName)}/load_session`, {method:'POST'})
      .then(() => { frame.contentWindow.location.replace(frame.src); })
      .catch(() => {});
  }
})();
                """.strip()
                return PlainTextResponse(js, media_type="application/javascript")

            @app.get("/")
            async def redirect_root_to_dev_ui():
                return RedirectResponse(redirect_dev_ui_url)

            @app.get("/dev-ui")
            async def redirect_dev_ui_add_slash():
                return RedirectResponse(redirect_dev_ui_url)

            @app.get("/dev-ui/")
            async def dev_ui_index_with_pause_button():
                index_html = (web_assets_dir / "index.html").read_text(encoding="utf-8")
                tooltip_style_tag = """
<style>
  /* Dark background to prevent white flash on iframe reload */
  html, body { background: #1e1e1e !important; }

  .mat-mdc-tooltip,
  .mdc-tooltip__surface {
    max-width: 420px !important;
  }

  .mdc-tooltip__surface {
    white-space: normal !important;
    overflow-wrap: anywhere;
  }
</style>
                """.strip()
                cdn_tag = '<script src="https://unpkg.com/vis-network@9.1.6/standalone/umd/vis-network.min.js"></script>'
                script_tag = (
                    '<script src="./opensage-stop-turn.js" type="module"></script>'
                    '<script src="./opensage-upload-sandbox.js" type="module"></script>'
                    '<script src="./opensage-subagent-panel.js" type="module"></script>'
                )
                injected = index_html.replace(
                    "</head>", f"{tooltip_style_tag}{cdn_tag}</head>"
                )
                injected = injected.replace("</body>", f"{script_tag}</body>")
                return HTMLResponse(content=injected)

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
