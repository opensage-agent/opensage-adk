from __future__ import annotations

import logging
import os
import re
import shlex
from pathlib import Path
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

# Host-side memory directory (always enabled, independent of memory management mode)
HOST_SESSION_ROOT = Path.home() / ".local" / "opensage" / "sessions"
HOST_MEM_DIR_KEY = "_host_mem_dir"

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
    # Host path: always set (for Web UI, regardless of memory mode)
    state[HOST_MEM_DIR_KEY] = compute_host_root_mem_dir(
        opensage_session_id=opensage_session_id,
        agent_name=agent_name,
        session_id=session_id,
    )
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


# ---------------------------------------------------------------------------
# Host-side functions (always enabled, for Web UI sub-agent visualization)
# ---------------------------------------------------------------------------


def compute_host_root_mem_dir(
    *, opensage_session_id: str, agent_name: str, session_id: str
) -> str:
    """Compute the host-side root agent directory path (always enabled)."""
    dir_name = _build_session_dir_name(agent_name, session_id)
    return str(HOST_SESSION_ROOT / opensage_session_id / "mem" / dir_name)


def _compute_host_child_mem_dir(
    parent_host_dir: str, *, child_agent_name: str, child_session_id: str
) -> str:
    """Compute host-side child directory, nested under parent."""
    child_dir_name = _build_session_dir_name(child_agent_name, child_session_id)
    return str(Path(parent_host_dir) / child_dir_name)


def _get_host_mem_dir(invocation_context) -> str | None:
    """Read host mem dir from session state. Returns None if not set."""
    state = getattr(invocation_context.session, "state", None)
    if isinstance(state, dict):
        val = state.get(HOST_MEM_DIR_KEY)
        if isinstance(val, str) and val:
            return val
    return None


def _ensure_host_mem_dir(host_dir: str) -> None:
    """Create directory on host (marks agent as running before traj.json exists)."""
    try:
        Path(host_dir).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("Failed to create host mem dir: %s", e)


def _persist_traj_json_to_host(invocation_context, host_dir: str) -> None:
    """Write traj.json to host filesystem."""
    try:
        path = Path(host_dir)
        path.mkdir(parents=True, exist_ok=True)
        session_json = invocation_context.session.model_dump_json(
            indent=2, exclude_none=True
        )
        (path / "traj.json").write_text(session_json, encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to persist traj.json to host: %s", e)


def scan_host_agent_tree(session_id: str) -> dict:
    """Scan host mem/ directory tree and return topology.

    Returns:
        {
            "root": {"name", "session_id", "dir", "has_traj", "children": [...]},
            "agents_flat": [{"name", "dir", "parent_name", "has_traj", "status"}, ...]
        }
    """
    mem_root = HOST_SESSION_ROOT / session_id / "mem"
    if not mem_root.exists():
        return {"root": None, "agents_flat": []}

    def _parse_dir_name(dirname: str) -> tuple[str, str]:
        if "__" in dirname:
            parts = dirname.split("__", 1)
            return parts[0], parts[1]
        return dirname, ""

    def _extract_query(traj_path: Path) -> str:
        """Extract the first user message text from traj.json as the call query."""
        try:
            import json as _j

            data = _j.loads(traj_path.read_text(encoding="utf-8"))
            for ev in data.get("events", []):
                content = ev.get("content") or {}
                if content.get("role") != "user":
                    continue
                for part in content.get("parts") or []:
                    text = part.get("text", "")
                    if text:
                        return text.strip()[:80]
                break
        except Exception:
            pass
        return ""

    def _scan(directory: Path) -> dict | None:
        if not directory.is_dir():
            return None
        agent_name, sid = _parse_dir_name(directory.name)
        traj_path = directory / "traj.json"
        has_traj = traj_path.exists()
        query = _extract_query(traj_path) if has_traj else ""
        children = []
        for child in sorted(directory.iterdir()):
            if child.is_dir() and "__" in child.name:
                child_node = _scan(child)
                if child_node:
                    children.append(child_node)
        return {
            "name": agent_name,
            "session_id": sid,
            "dir": str(directory.relative_to(mem_root)),
            "has_traj": has_traj,
            "query": query,
            "children": children,
        }

    agents_flat: list[dict] = []

    def _flatten(node: dict, parent_name: str = "") -> None:
        agents_flat.append(
            {
                "name": node["name"],
                "session_id": node["session_id"],
                "dir": node["dir"],
                "parent_name": parent_name,
                "has_traj": node["has_traj"],
                "query": node.get("query", ""),
                "status": "completed" if node["has_traj"] else "running",
            }
        )
        for child in node.get("children", []):
            _flatten(child, parent_name=node["name"])

    top_dirs = [d for d in mem_root.iterdir() if d.is_dir() and "__" in d.name]
    if not top_dirs:
        return {"root": None, "agents_flat": []}

    root_node = _scan(top_dirs[0])
    if root_node:
        _flatten(root_node)
    return {"root": root_node, "agents_flat": agents_flat}
