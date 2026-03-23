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
            app_name: str, user_id: str, req: Optional[CreateSessionRequest] = None
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
                    state=(req.state if req else None),
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
            # Only expose the fixed session for this app/user.
            session = await self.session_service.get_session(
                app_name=app_name, user_id=user_id, session_id=self.fixed_session_id
            )
            if not session:
                # Lazily ensure it exists
                session = await self.session_service.create_session(
                    app_name=app_name,
                    user_id=user_id,
                    state=None,
                    session_id=self.fixed_session_id,
                )
            return [session]

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
                script_tag = (
                    '<script src="./opensage-stop-turn.js" type="module"></script>'
                    '<script src="./opensage-upload-sandbox.js" type="module"></script>'
                )
                injected = index_html.replace("</head>", f"{tooltip_style_tag}</head>")
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
