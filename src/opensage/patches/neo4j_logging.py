from __future__ import annotations

import copy
import json
import logging
import os
import re
import shlex
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.run_config import RunConfig
from google.adk.apps.app import App
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.tools._forwarding_artifact_service import ForwardingArtifactService
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

logger = logging.getLogger(__name__)

_enabled: bool = False
_patched: bool = False
_orig_base_agent_run: Optional[Callable] = None
_MEM_ROOT_DIR = "/mem"
_SHORT_TERM_MEM_ROOT = os.path.join(_MEM_ROOT_DIR, "short_term")
_LONG_TERM_MEM_ROOT = os.path.join(_MEM_ROOT_DIR, "long_term")
_LONG_TERM_KNOWLEDGE_DIR = os.path.join(_LONG_TERM_MEM_ROOT, "knowledge")
_LONG_TERM_KNOWLEDGE_PATH = os.path.join(_LONG_TERM_KNOWLEDGE_DIR, "knowledge.json")
_MEM_AGENT_DIR_KEY = "_mem_agent_dir"
_MEM_TOPOLOGY_PATH = os.path.join(_MEM_ROOT_DIR, "topology.json")
_FILE_MEMORY_CONTEXT_START = "[[OPENSAGE_FILE_MEMORY_CONTEXT]]"
_FILE_MEMORY_CONTEXT_END = "[[/OPENSAGE_FILE_MEMORY_CONTEXT]]"


def _sanitize_name(name: str) -> str:
    """Return a filesystem-safe agent name component."""
    if not name:
        return "agent"
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("._-")
    return safe_name or "agent"


def _build_session_dir_name(agent_name: str, session_id: str) -> str:
    """Return the directory name for one agent session."""
    return f"{_sanitize_name(agent_name)}__{session_id}"


def _compute_root_session_mem_dir(invocation_context) -> str:
    """Compute the root short-term memory directory for a session."""
    session = invocation_context.session
    agent_name = _sanitize_name(getattr(invocation_context.agent, "name", "agent"))
    return os.path.join(
        _SHORT_TERM_MEM_ROOT,
        _build_session_dir_name(agent_name, session.id),
    )


def _compute_agent_mem_dir(invocation_context) -> str:
    """Return the current session memory directory from session.state."""
    session = invocation_context.session
    state = getattr(session, "state", None)
    if not isinstance(state, dict):
        raise ValueError("Session state must be a dict to resolve memory directory")
    existing = state.get(_MEM_AGENT_DIR_KEY)
    if (
        isinstance(existing, str)
        and existing
        and existing.startswith(_SHORT_TERM_MEM_ROOT)
    ):
        return existing
    raise ValueError("Session memory directory missing from session.state")


def get_current_session_mem_dir(context) -> str:
    """Return the current session directory for an invocation or tool context."""
    invocation_context = getattr(context, "_invocation_context", context)
    return _compute_agent_mem_dir(invocation_context)


def get_current_session_tool_outputs_dir(context) -> str:
    """Return the current session tool output directory."""
    return os.path.join(get_current_session_mem_dir(context), "tool_outputs")


def _compute_child_session_mem_dir(
    parent_invocation_context, *, child_agent_name: str, child_session_id: str
) -> str:
    """Compute the nested child session directory under the caller session."""
    parent_dir = _compute_agent_mem_dir(parent_invocation_context)
    child_dir_name = _build_session_dir_name(child_agent_name, child_session_id)
    return os.path.join(parent_dir, child_dir_name)


def _get_main_sandbox(invocation_context):
    """Return main sandbox instance for current OpenSage session."""
    from opensage.session import get_opensage_session
    from opensage.utils.agent_utils import get_opensage_session_id_from_context

    opensage_session_id = get_opensage_session_id_from_context(invocation_context)
    opensage_session = get_opensage_session(opensage_session_id)
    return opensage_session.sandboxes.get_sandbox("main")


def _write_text_to_main_sandbox(
    invocation_context, container_path: str, text: str
) -> None:
    """Write text into main sandbox via temporary host file."""
    sandbox = _get_main_sandbox(invocation_context)
    parent_dir = os.path.dirname(container_path)
    sandbox.run_command_in_container(f"mkdir -p {shlex.quote(parent_dir)}")
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as temp_file:
        temp_file.write(text)
        local_path = temp_file.name
    try:
        sandbox.copy_file_to_container(local_path, container_path)
    finally:
        try:
            os.unlink(local_path)
        except OSError:
            logger.debug("Failed to cleanup temp file: %s", local_path)


def _ensure_file_memory_roots(invocation_context) -> None:
    """Create shared file-memory roots in the main sandbox."""
    sandbox = _get_main_sandbox(invocation_context)
    sandbox.run_command_in_container(
        "mkdir -p "
        f"{shlex.quote(_SHORT_TERM_MEM_ROOT)} "
        f"{shlex.quote(_LONG_TERM_KNOWLEDGE_DIR)}"
    )
    _, exit_code = sandbox.run_command_in_container(
        f"test -f {shlex.quote(_LONG_TERM_KNOWLEDGE_PATH)}"
    )
    if exit_code != 0:
        _write_text_to_main_sandbox(
            invocation_context, _LONG_TERM_KNOWLEDGE_PATH, "{}\n"
        )


def _ensure_agent_mem_layout(
    invocation_context, agent_mem_dir: str, *, agent_name: str
) -> None:
    """Create the current session folder and default TODO.md in main sandbox."""
    sandbox = _get_main_sandbox(invocation_context)
    _ensure_file_memory_roots(invocation_context)
    sandbox.run_command_in_container(
        f"mkdir -p {shlex.quote(agent_mem_dir)} "
        f"{shlex.quote(os.path.join(agent_mem_dir, 'tool_outputs'))}"
    )
    todo_path = os.path.join(agent_mem_dir, "TODO.md")
    _, exit_code = sandbox.run_command_in_container(f"test -f {shlex.quote(todo_path)}")
    if exit_code == 0:
        return
    todo_seed = (
        f"# TODO for {agent_name}\n\n"
        "- [ ] Capture the current task\n"
        "- [ ] Update progress as work proceeds\n"
    )
    _write_text_to_main_sandbox(invocation_context, todo_path, todo_seed)


def _persist_traj_json(invocation_context, agent_mem_dir: str) -> None:
    """Persist full ADK session JSON into traj.json."""
    session_json = invocation_context.session.model_dump_json(
        indent=2, exclude_none=True
    )
    traj_json_path = os.path.join(agent_mem_dir, "traj.json")
    _write_text_to_main_sandbox(invocation_context, traj_json_path, session_json)


def persist_traj_json_for_invocation(invocation_context) -> None:
    """Persist traj.json for the current invocation context."""
    _persist_traj_json(invocation_context, _compute_agent_mem_dir(invocation_context))


def _is_file_memory_enabled(agent: BaseAgent) -> bool:
    """Return whether the agent is configured to use file memory."""
    memory_management = getattr(agent, "_memory_management", None)
    return getattr(memory_management, "value", memory_management) == "file"


def _strip_runtime_file_memory_context(instruction: str) -> str:
    """Remove any previously injected runtime file-memory context block."""
    if not instruction:
        return ""
    pattern = (
        rf"\n\n{re.escape(_FILE_MEMORY_CONTEXT_START)}.*?"
        rf"{re.escape(_FILE_MEMORY_CONTEXT_END)}"
    )
    return re.sub(pattern, "", instruction, flags=re.DOTALL)


def _build_runtime_file_memory_context(*, session_id: str, agent_mem_dir: str) -> str:
    """Build the session-specific file-memory prompt block."""
    return (
        f"{_FILE_MEMORY_CONTEXT_START}\n"
        "### Current File Memory Session\n"
        f"- Your current `session_id` is `{session_id}`.\n"
        f"- Your current short-term memory directory is `{agent_mem_dir}`.\n"
        f"- Keep your working notes in `{os.path.join(agent_mem_dir, 'TODO.md')}`.\n"
        f"- Your full trajectory file is `{os.path.join(agent_mem_dir, 'traj.json')}`.\n"
        f"- Save long tool outputs under `{os.path.join(agent_mem_dir, 'tool_outputs')}/`.\n"
        f"- Shared long-term knowledge lives at `{_LONG_TERM_KNOWLEDGE_PATH}`.\n"
        f"{_FILE_MEMORY_CONTEXT_END}"
    )


def _inject_runtime_file_memory_context(
    agent: BaseAgent, *, session_id: str, agent_mem_dir: str
) -> str | None:
    """Inject session-specific file-memory context into the agent instruction."""
    if not _is_file_memory_enabled(agent) or not hasattr(agent, "instruction"):
        return None

    original_instruction = getattr(agent, "instruction", "") or ""
    stripped_instruction = _strip_runtime_file_memory_context(original_instruction)
    runtime_context = _build_runtime_file_memory_context(
        session_id=session_id, agent_mem_dir=agent_mem_dir
    )
    agent.instruction = f"{stripped_instruction}\n\n{runtime_context}"
    return original_instruction


def _clone_agent_for_child_session(
    agent: BaseAgent, *, session_id: str, agent_mem_dir: str
) -> BaseAgent:
    """Shallow copy the child agent before injecting session-specific prompt text."""
    try:
        cloned_agent = agent.model_copy(deep=False)
    except Exception:
        cloned_agent = copy.copy(agent)
    _inject_runtime_file_memory_context(
        cloned_agent, session_id=session_id, agent_mem_dir=agent_mem_dir
    )
    return cloned_agent


def _now_iso_utc() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _extract_query_from_invocation_context(invocation_context) -> str:
    """Best-effort query extraction from current invocation."""
    try:
        user_content = getattr(invocation_context, "user_content", None)
        if user_content and getattr(user_content, "parts", None):
            for part in reversed(user_content.parts):
                text = getattr(part, "text", "")
                if isinstance(text, str) and text:
                    return text
    except Exception:
        logger.debug("Failed to extract query from invocation_context.user_content")
    return ""


def _load_topology_data(invocation_context) -> dict[str, Any]:
    """Load /mem/topology.json from main sandbox."""
    sandbox = _get_main_sandbox(invocation_context)
    try:
        raw = (sandbox.extract_file_from_container(_MEM_TOPOLOGY_PATH) or "").strip()
    except Exception:
        raw = ""
    if not raw:
        return {"agents": [], "calls": []}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid topology.json, resetting topology data")
        return {"agents": [], "calls": []}
    if not isinstance(data, dict):
        return {"agents": [], "calls": []}
    agents = data.get("agents", [])
    calls = data.get("calls", [])
    if not isinstance(agents, list):
        agents = []
    if not isinstance(calls, list):
        calls = []
    return {"agents": agents, "calls": calls}


def _save_topology_data(invocation_context, topology_data: dict[str, Any]) -> None:
    """Persist /mem/topology.json into main sandbox."""
    topology_data["updated_at"] = _now_iso_utc()
    payload = json.dumps(topology_data, indent=2, ensure_ascii=False)
    _write_text_to_main_sandbox(invocation_context, _MEM_TOPOLOGY_PATH, payload)


def _upsert_agent_record(topology_data: dict[str, Any], record: dict[str, Any]) -> None:
    """Upsert an agent record by session_id."""
    session_id = record.get("session_id")
    if not session_id:
        return
    agents = topology_data.setdefault("agents", [])
    for existing in agents:
        if existing.get("session_id") == session_id:
            # Avoid clobbering useful values with empty placeholders.
            for key, value in record.items():
                if value is None:
                    continue
                if isinstance(value, str) and value == "":
                    continue
                existing[key] = value
            return
    agents.append(record)


def _infer_parent_links_from_calls(topology_data: dict[str, Any]) -> None:
    """Infer parent fields from call relationships."""
    agents = topology_data.get("agents", [])
    calls = topology_data.get("calls", [])
    if not isinstance(agents, list) or not isinstance(calls, list):
        return
    by_session_id = {
        agent.get("session_id"): agent
        for agent in agents
        if isinstance(agent, dict) and agent.get("session_id")
    }
    for call in calls:
        if not isinstance(call, dict):
            continue
        caller_session_id = call.get("caller_session_id")
        caller_agent_name = call.get("caller_agent_name")
        callee_session_id = call.get("callee_session_id")
        if not (
            isinstance(caller_session_id, str)
            and caller_session_id
            and isinstance(callee_session_id, str)
            and callee_session_id
        ):
            continue
        callee = by_session_id.get(callee_session_id)
        if not isinstance(callee, dict):
            continue
        if not callee.get("parent_session_id"):
            callee["parent_session_id"] = caller_session_id
        if (
            not callee.get("parent_agent_name")
            and isinstance(caller_agent_name, str)
            and caller_agent_name
        ):
            callee["parent_agent_name"] = caller_agent_name


def _sync_parent_links(topology_data: dict[str, Any]) -> None:
    """Sync parent fields from inferred call relationships."""
    _infer_parent_links_from_calls(topology_data)


def _record_topology_agent_start(
    invocation_context, *, session_id: str, agent_name: str, query: str
) -> None:
    """Upsert topology agent start info."""
    topology_data = _load_topology_data(invocation_context)
    _upsert_agent_record(
        topology_data,
        {
            "session_id": session_id,
            "agent_name": agent_name,
            "query": query or "",
            "response": "",
            "status": "running",
            "updated_at": _now_iso_utc(),
        },
    )
    _sync_parent_links(topology_data)
    _save_topology_data(invocation_context, topology_data)


def _record_topology_agent_end(
    invocation_context, *, session_id: str, response: str, status: str
) -> None:
    """Update topology agent completion info."""
    topology_data = _load_topology_data(invocation_context)
    _upsert_agent_record(
        topology_data,
        {
            "session_id": session_id,
            "response": response or "",
            "status": status,
            "updated_at": _now_iso_utc(),
        },
    )
    _sync_parent_links(topology_data)
    _save_topology_data(invocation_context, topology_data)


def _record_topology_call(
    invocation_context,
    *,
    caller_session_id: str,
    caller_agent_name: str,
    callee_session_id: str,
    callee_agent_name: str,
    query: str,
) -> None:
    """Append topology call relationship and keep parent links synchronized."""
    topology_data = _load_topology_data(invocation_context)
    calls = topology_data.setdefault("calls", [])
    if not isinstance(calls, list):
        calls = []
        topology_data["calls"] = calls
    _upsert_agent_record(
        topology_data,
        {
            "session_id": caller_session_id,
            "agent_name": caller_agent_name,
            "updated_at": _now_iso_utc(),
        },
    )
    _upsert_agent_record(
        topology_data,
        {
            "session_id": callee_session_id,
            "agent_name": callee_agent_name,
            "parent_session_id": caller_session_id,
            "parent_agent_name": caller_agent_name,
            "updated_at": _now_iso_utc(),
        },
    )
    calls.append(
        {
            "caller_session_id": caller_session_id,
            "caller_agent_name": caller_agent_name,
            "callee_session_id": callee_session_id,
            "callee_agent_name": callee_agent_name,
            "query": query or "",
            "updated_at": _now_iso_utc(),
        }
    )
    _sync_parent_links(topology_data)
    _save_topology_data(invocation_context, topology_data)


async def _record_agent_call(
    agent_tool: AgentTool,
    *,
    agent_tool_session_id: str,
    args,
    tool_context: ToolContext,
):
    """Create the agent call relationship before executing."""
    caller_agent_name = tool_context._invocation_context.agent.name
    callee_agent_name = agent_tool.agent.name
    caller_session_id = tool_context._invocation_context.session.id
    callee_session_id = agent_tool_session_id
    # Convert args to string for input_context
    input_content = args.get("request", "")
    if _enabled:
        # Lazy import to avoid circular imports during bootstrap
        from opensage.utils.neo4j_history_management import (  # type: ignore
            create_agent_call_relation,
        )

        caller_agent_model = (
            tool_context._invocation_context.agent.model
            if hasattr(tool_context._invocation_context.agent, "model")
            and isinstance(tool_context._invocation_context.agent.model, str)
            else tool_context._invocation_context.agent.model.model
            if hasattr(tool_context._invocation_context.agent, "model")
            else "No model"
        )
        callee_agent_model = (
            agent_tool.agent.model
            if hasattr(agent_tool.agent, "model")
            and isinstance(agent_tool.agent.model, str)
            else agent_tool.agent.model.model
            if hasattr(agent_tool.agent, "model")
            else "No model"
        )
        try:
            await create_agent_call_relation(
                caller_agent_name=caller_agent_name,
                callee_agent_name=callee_agent_name,
                caller_session_id=caller_session_id,
                callee_session_id=callee_session_id,
                input_content=input_content,
                output_content="",
                caller_agent_model=caller_agent_model,
                callee_agent_model=callee_agent_model,
                context=tool_context,
            )
        except Exception as e:
            logger.error(f"Failed to create agent call relation: {e}")
    try:
        _record_topology_call(
            tool_context._invocation_context,
            caller_session_id=caller_session_id,
            caller_agent_name=caller_agent_name,
            callee_session_id=callee_session_id,
            callee_agent_name=callee_agent_name,
            query=input_content,
        )
    except Exception as topology_error:
        logger.warning("Failed to record topology call: %s", topology_error)


async def _wrapped_base_agent_run(self, invocation_context):
    logging_enabled = _enabled
    session_id = invocation_context.session.id
    query = _extract_query_from_invocation_context(invocation_context)
    session_state = getattr(invocation_context.session, "state", None)
    if not isinstance(session_state, dict):
        raise ValueError("Session state must be a dict for file memory layout")
    agent_mem_dir = session_state.get(_MEM_AGENT_DIR_KEY)
    if not (
        isinstance(agent_mem_dir, str)
        and agent_mem_dir
        and agent_mem_dir.startswith(_SHORT_TERM_MEM_ROOT)
    ):
        agent_mem_dir = _compute_root_session_mem_dir(invocation_context)
        session_state[_MEM_AGENT_DIR_KEY] = agent_mem_dir
    original_instruction = None
    try:
        _ensure_agent_mem_layout(
            invocation_context,
            agent_mem_dir,
            agent_name=getattr(invocation_context.agent, "name", "agent"),
        )
    except Exception as mem_error:
        logger.warning("Failed to initialize agent memory dir: %s", mem_error)
    try:
        original_instruction = _inject_runtime_file_memory_context(
            self, session_id=session_id, agent_mem_dir=agent_mem_dir
        )
    except Exception as prompt_error:
        logger.warning("Failed to inject file-memory prompt context: %s", prompt_error)
    try:
        _record_topology_agent_start(
            invocation_context,
            session_id=session_id,
            agent_name=getattr(invocation_context.agent, "name", "agent"),
            query=query,
        )
    except Exception as topology_error:
        logger.warning("Failed to record topology agent start: %s", topology_error)

    if logging_enabled:
        from opensage.utils.neo4j_history_management import (  # type: ignore
            find_agent_run_by_session_id,
            log_single_event_neo4j,
            record_agent_end,
            record_agent_start,
            store_session_state,
        )

        await record_agent_start(self, invocation_context)

    last_event = None
    run_failed = False
    try:
        async for event in _orig_base_agent_run(self, invocation_context):
            if logging_enabled:
                try:
                    await log_single_event_neo4j(event, session_id, invocation_context)
                except Exception as event_error:
                    exc_type, exc_value, exc_traceback = sys.exc_info()
                    if exc_traceback:
                        traceback.print_tb(exc_traceback)
                    logger.error(f"Failed to process event: {event_error}")
                    raise

            last_event = event
            yield event

    except Exception as e:
        run_failed = True
        if logging_enabled:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            if exc_traceback:
                traceback.print_tb(exc_traceback)
            logger.error(f"Failed to record agent run: {e}")
            await record_agent_end(invocation_context, "", "error")
        raise

    finally:
        try:
            _persist_traj_json(invocation_context, agent_mem_dir)
        except Exception as mem_error:
            logger.warning("Failed to persist traj.json: %s", mem_error)

        if logging_enabled:
            try:
                final_session_state = invocation_context.session.state
                found_session = await find_agent_run_by_session_id(
                    session_id, invocation_context
                )
                if found_session:
                    await store_session_state(
                        session_id, final_session_state, invocation_context
                    )
            except Exception as state_error:
                exc_type, exc_value, exc_traceback = sys.exc_info()
                if exc_traceback:
                    traceback.print_tb(exc_traceback)
                logger.error(f"Failed to store final session state: {state_error}")

            try:
                output_content = ""
                if last_event and last_event.content and last_event.content.parts:
                    output_content = "\n".join(
                        p.text
                        for p in last_event.content.parts
                        if hasattr(p, "text") and p.text
                    )
                if not run_failed:
                    await record_agent_end(
                        invocation_context, output_content, "completed"
                    )
            except Exception as e:
                exc_type, exc_value, exc_traceback = sys.exc_info()
                if exc_traceback:
                    traceback.print_tb(exc_traceback)
                logger.error(f"Failed to record agent end: {e}")
                await record_agent_end(invocation_context, "", "error")

        # File topology is independent from Neo4j logging toggle.
        output_content = ""
        if last_event and last_event.content and last_event.content.parts:
            output_content = "\n".join(
                p.text
                for p in last_event.content.parts
                if hasattr(p, "text") and p.text
            )
        topology_status = "error" if run_failed else "completed"
        try:
            _record_topology_agent_end(
                invocation_context,
                session_id=session_id,
                response=output_content,
                status=topology_status,
            )
        except Exception as topology_error:
            logger.warning("Failed to record topology agent end: %s", topology_error)

        # Write child's used llm calls into its session.state for parent to read
        try:
            used_child = int(
                getattr(
                    getattr(invocation_context, "_invocation_cost_manager", None),
                    "_number_of_llm_calls",
                    0,
                )
                or 0
            )
            invocation_context.session.state.setdefault("_adk", {})
            invocation_context.session.state["_adk"]["llm_calls_used"] = used_child
        except Exception as _e:
            logger.debug(f"skip writing child llm_calls_used: {_e}")

        if original_instruction is not None:
            self.instruction = original_instruction


def apply() -> None:
    """Monkey-patch BaseAgent.run_async and AgentTool.run_async with toggle."""
    global _patched, _orig_base_agent_run
    if _patched:
        return

    _orig_base_agent_run = BaseAgent.run_async

    async def _run_child_agent(
        agent_tool: AgentTool,
        args: dict[str, Any],
        tool_context: ToolContext,
    ) -> tuple[Any, Any]:
        """Execute the wrapped agent and return (last_event, child_session)."""
        if agent_tool.skip_summarization:
            tool_context.actions.skip_summarization = True

        if isinstance(agent_tool.agent, LlmAgent) and agent_tool.agent.input_schema:
            input_value = agent_tool.agent.input_schema.model_validate(args)
            content = types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=input_value.model_dump_json(exclude_none=True)
                    )
                ],
            )
        else:
            content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=args["request"])],
            )
        from opensage.features.opensage_in_memory_session_service import (
            OpenSageInMemorySessionService,
        )

        session_service = OpenSageInMemorySessionService()
        session = await session_service.create_session(
            app_name=agent_tool.agent.name,
            user_id="tmp_user",
            state=tool_context.state.to_dict(),
        )
        child_agent_mem_dir = _compute_child_session_mem_dir(
            tool_context._invocation_context,
            child_agent_name=agent_tool.agent.name,
            child_session_id=session.id,
        )
        session.state[_MEM_AGENT_DIR_KEY] = child_agent_mem_dir
        try:
            _ensure_agent_mem_layout(
                tool_context._invocation_context,
                child_agent_mem_dir,
                agent_name=agent_tool.agent.name,
            )
        except Exception as mem_error:
            logger.warning("Failed to initialize child agent memory dir: %s", mem_error)

        parent_plugins = []
        try:
            parent_plugins = list(
                tool_context._invocation_context.plugin_manager.plugins
            )
        except Exception as plugin_error:
            logger.debug("Failed to reuse parent plugins: %s", plugin_error)

        child_agent = _clone_agent_for_child_session(
            agent_tool.agent,
            session_id=session.id,
            agent_mem_dir=child_agent_mem_dir,
        )
        agentic_app = App(
            name=child_agent.name,
            root_agent=child_agent,
            plugins=parent_plugins,
        )

        runner = Runner(
            app=agentic_app,
            artifact_service=ForwardingArtifactService(tool_context),
            session_service=session_service,
            memory_service=InMemoryMemoryService(),
            credential_service=tool_context._invocation_context.credential_service,
        )

        await _record_agent_call(
            agent_tool=agent_tool,
            agent_tool_session_id=session.id,
            args=args,
            tool_context=tool_context,
        )

        parent_ctx = tool_context._invocation_context
        limit = int(
            getattr(getattr(parent_ctx, "run_config", None), "max_llm_calls", 0) or 0
        )
        used = int(
            getattr(
                getattr(parent_ctx, "_invocation_cost_manager", None),
                "_number_of_llm_calls",
                0,
            )
            or 0
        )
        remaining = min(50, max(0, (limit - used) if limit > 0 else 50))
        remaining_for_this_child = remaining

        last_event = None
        try:
            async for event in runner.run_async(
                user_id=session.user_id,
                session_id=session.id,
                new_message=content,
                run_config=RunConfig(max_llm_calls=remaining),
            ):
                try:
                    logger.warning(
                        f"[SUBAGENT:{agent_tool.agent.name}] {event.model_dump_json(exclude_none=True)}"
                    )
                except Exception as json_error:
                    # Handle Neo4j DateTime serialization error
                    logger.warning(
                        f"[SUBAGENT:{agent_tool.agent.name}] Event serialization failed: {json_error}, "
                        f"event_id={event.id}, event_type={getattr(event, 'type', 'unknown')}"
                    )
                if event.actions.state_delta:
                    tool_context.state.update(event.actions.state_delta)
                last_event = event
        except Exception as run_error:
            # Do not raise. Ask the model for a final summary using the session history.
            logger.error(f"Subagent run error, switching to final summary: {run_error}")
            summary_prompt = (
                "Final summary requested due to internal error.\n\n"
                "Instructions:\n"
                "1) Provide a concise final summary based ONLY on the existing history.\n"
                "2) Include sections: Summary, What was verified, Next steps, Known blockers.\n"
                "3) Do not speculate beyond the evidence.\n\n"
                f"We just encountered an error: {repr(run_error)}\n"
            )
            summary_content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=summary_prompt)],
            )
            fallback_last_event = None
            async for ev in runner.run_async(
                user_id=session.user_id,
                session_id=session.id,
                new_message=summary_content,
                run_config=RunConfig(max_llm_calls=min(remaining, 5)),
            ):
                fallback_last_event = ev
            if fallback_last_event:
                last_event = fallback_last_event
            # if child agent tool raises an error, we consider it has used all the remaining llm calls
            session.state["_adk"]["llm_calls_used"] = remaining_for_this_child

        return last_event, session

    async def _wrapped_agent_tool_run(
        self,
        *,
        args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Any:
        logger.warning(
            "[AgentToolPatch] invoked for agent=%s enabled=%s",
            getattr(self.agent, "name", "unknown"),
            _enabled,
        )
        last_event, child_session = await _run_child_agent(self, args, tool_context)

        # Merge child's actually used llm calls back to parent for accurate countdown
        try:
            parent_ctx = tool_context._invocation_context
            parent_mgr = getattr(parent_ctx, "_invocation_cost_manager", None)
            parent_limit = int(
                getattr(getattr(parent_ctx, "run_config", None), "max_llm_calls", 0)
                or 0
            )
            child_used = int(
                (
                    child_session
                    and child_session.state
                    and child_session.state.get("_adk", {}).get("llm_calls_used", 0)
                )
                or 0
            )
            incremented = False
            try:
                for _ in range(child_used):
                    parent_ctx.increment_llm_call_count()
                incremented = True
            except Exception as limit_err:
                # If parent_ctx.increment_llm_call_count() exists, it should be the
                # authoritative way to account for usage (it may enforce limits).
                # Do not also add child_used again below, or we'll double count.
                logger.debug(
                    "Unable to increment parent LLM call count while merging child usage: %s",
                    limit_err,
                )

            if not incremented:
                # Fallback for contexts that don't expose increment_llm_call_count()
                # (e.g., some test stubs): adjust the counter directly once.
                parent_used_now = int(
                    getattr(parent_mgr, "_number_of_llm_calls", 0) or 0
                )
                if parent_limit > 0:
                    setattr(
                        parent_mgr,
                        "_number_of_llm_calls",
                        min(parent_limit, parent_used_now + child_used),
                    )
                else:
                    setattr(
                        parent_mgr, "_number_of_llm_calls", parent_used_now + child_used
                    )
        except Exception as _e:
            logger.debug(f"skip merging child llm_calls_used: {_e}")

        if not last_event or not last_event.content or not last_event.content.parts:
            return ""
        merged_text = "\n".join(p.text for p in last_event.content.parts if p.text)
        if isinstance(self.agent, LlmAgent) and self.agent.output_schema:
            tool_result = self.agent.output_schema.model_validate_json(
                merged_text
            ).model_dump(exclude_none=True)
        else:
            tool_result = merged_text
        return tool_result

    AgentTool.run_async = _wrapped_agent_tool_run
    BaseAgent.run_async = _wrapped_base_agent_run
    _patched = True


def enable() -> None:
    global _enabled
    _enabled = True


def disable() -> None:
    global _enabled
    _enabled = False


def is_enabled() -> bool:
    return _enabled
