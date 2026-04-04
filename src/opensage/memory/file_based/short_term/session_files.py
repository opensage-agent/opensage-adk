from __future__ import annotations

import logging
import os
import re
import shlex
from typing import Any, Optional

from opensage.memory.file_based.long_term import ensure_long_term_knowledge_store
from opensage.memory.file_based.short_term.sandbox_io import (
    _get_main_sandbox,
    _write_text_to_main_sandbox,
)

logger = logging.getLogger(__name__)

MEM_ROOT_DIR = "/mem"
SHORT_TERM_MEM_ROOT = os.path.join(MEM_ROOT_DIR, "short_term")
MEM_AGENT_DIR_KEY = "_mem_agent_dir"

_MEMORY_MANAGEMENT_FILE = "file"


def _sanitize_name(name: str) -> str:
    """Return a filesystem-safe agent name component."""
    if not name:
        return "agent"
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("._-")
    return safe_name or "agent"


def _build_session_dir_name(agent_name: str, session_id: str) -> str:
    """Return the directory name for one agent session."""
    return f"{_sanitize_name(agent_name)}__{session_id}"


def compute_root_session_mem_dir(*, agent_name: str, session_id: str) -> str:
    """Compute the root short-term memory directory from agent/session ids."""
    return os.path.join(
        SHORT_TERM_MEM_ROOT,
        _build_session_dir_name(agent_name, session_id),
    )


def _normalize_memory_management(memory_management: Any) -> str:
    """Normalize configured memory management mode."""
    value = getattr(memory_management, "value", memory_management)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == _MEMORY_MANAGEMENT_FILE:
            return normalized
    return _MEMORY_MANAGEMENT_FILE


def _get_memory_management_from_opensage_session_id(opensage_session_id: str) -> str:
    """Read memory management mode from the OpenSage session config."""
    from opensage.session import get_opensage_session

    opensage_session = get_opensage_session(opensage_session_id)
    memory_config = getattr(getattr(opensage_session, "config", None), "memory", None)
    return _normalize_memory_management(getattr(memory_config, "management", None))


def build_root_session_state(
    *,
    opensage_session_id: str,
    session_id: str,
    agent_name: str,
    base_state: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build canonical root session state for file-based memory."""
    state = dict(base_state or {})
    state["opensage_session_id"] = opensage_session_id
    if (
        _get_memory_management_from_opensage_session_id(opensage_session_id)
        == _MEMORY_MANAGEMENT_FILE
    ):
        state[MEM_AGENT_DIR_KEY] = compute_root_session_mem_dir(
            agent_name=agent_name,
            session_id=session_id,
        )
    else:
        state.pop(MEM_AGENT_DIR_KEY, None)
    return state


def _compute_agent_mem_dir(invocation_context) -> str:
    """Return the current session memory directory from session.state."""
    session = invocation_context.session
    state = getattr(session, "state", None)
    if not isinstance(state, dict):
        raise ValueError("Session state must be a dict to resolve memory directory")
    existing = state.get(MEM_AGENT_DIR_KEY)
    if (
        isinstance(existing, str)
        and existing
        and existing.startswith(SHORT_TERM_MEM_ROOT)
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


def _ensure_file_memory_roots(invocation_context) -> None:
    """Create shared file-memory roots in the main sandbox."""
    sandbox = _get_main_sandbox(invocation_context)
    sandbox.run_command_in_container(f"mkdir -p {shlex.quote(SHORT_TERM_MEM_ROOT)}")
    ensure_long_term_knowledge_store(invocation_context)


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
