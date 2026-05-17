from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from pathlib import Path
from typing import Any, Optional

from opensage.memory.file_based.long_term import ensure_long_term_knowledge_store
from opensage.memory.file_based.short_term.sandbox_io import (
    _get_main_sandbox,
    _write_text_to_main_sandbox,
)
from opensage.utils.agent_utils import sanitize_agent_name

logger = logging.getLogger(__name__)

MEM_AGENT_DIR_KEY = "_mem_agent_dir"


def _mem_root() -> str:
    """Return the configured mem_root (sandbox-side path)."""
    from opensage.sandbox.sandbox_paths import get_mem_root

    return get_mem_root()


def _short_term_root(mem_root: str | None = None) -> str:
    return os.path.join(mem_root or _mem_root(), "short_term")


# Host-side instance directory.
HOST_SESSION_ROOT = Path.home() / ".local" / "opensage" / "sessions"
HOST_INSTANCE_DIR_KEY = "_host_instance_dir"
HOST_INSTANCES_SUBDIR = "instances"


def _build_session_dir_name(agent_name: str, session_id: str) -> str:
    """Return the directory name for one agent session in the sandbox.

    Sandbox-side layout is unchanged: ``{sanitized_name}__{sid}``.
    Host side uses pure ``sid`` and does not go through this helper.
    """
    return f"{sanitize_agent_name(agent_name)}__{session_id}"


def compute_root_session_mem_dir(
    *, agent_name: str, session_id: str, mem_root: str | None = None
) -> str:
    """Compute the root short-term memory directory inside the sandbox."""
    return os.path.join(
        _short_term_root(mem_root),
        _build_session_dir_name(agent_name, session_id),
    )


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
    state[MEM_AGENT_DIR_KEY] = compute_root_session_mem_dir(
        agent_name=agent_name,
        session_id=session_id,
    )
    # Host instance dir: pure sid, no agent_name prefix
    state[HOST_INSTANCE_DIR_KEY] = compute_host_root_instance_dir(
        opensage_session_id=opensage_session_id,
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
    if isinstance(existing, str) and existing:
        return existing
    raise ValueError("Session memory directory missing from session.state")


def get_current_session_mem_dir(context) -> str:
    """Return the current session directory for an invocation or tool context."""
    invocation_context = getattr(context, "_invocation_context", context)
    return _compute_agent_mem_dir(invocation_context)


def get_current_session_tool_outputs_dir(context) -> str:
    """Return the current session tool output directory."""
    return os.path.join(get_current_session_mem_dir(context), "tool_outputs")


def get_current_session_pinned_path(context) -> str:
    """Return the path to pinned.md for the current session.

    pinned.md holds task-specific information (credentials, exact URLs,
    step-by-step procedures) that must survive history compaction verbatim.
    Its content is embedded into compaction events so it is never lost to
    LLM summarization.
    """
    return os.path.join(get_current_session_mem_dir(context), "pinned.md")


def _compute_child_session_mem_dir(
    parent_invocation_context, *, child_agent_name: str, child_session_id: str
) -> str:
    """Compute the nested child session directory under the caller session (sandbox)."""
    parent_dir = _compute_agent_mem_dir(parent_invocation_context)
    child_dir_name = _build_session_dir_name(child_agent_name, child_session_id)
    return os.path.join(parent_dir, child_dir_name)


async def _ensure_file_memory_roots(invocation_context) -> None:
    """Create shared file-memory roots in the main sandbox."""
    sandbox = _get_main_sandbox(invocation_context)
    await sandbox.arun_command_in_container(
        f"mkdir -p {shlex.quote(_short_term_root())}"
    )
    await ensure_long_term_knowledge_store(invocation_context)


async def _ensure_agent_mem_layout(
    invocation_context, agent_mem_dir: str, *, agent_name: str
) -> None:
    """Create the current session folder, TODO.md, and pinned.md in main sandbox."""
    sandbox = _get_main_sandbox(invocation_context)
    await _ensure_file_memory_roots(invocation_context)
    await sandbox.arun_command_in_container(
        f"mkdir -p {shlex.quote(agent_mem_dir)} "
        f"{shlex.quote(os.path.join(agent_mem_dir, 'tool_outputs'))}"
    )
    todo_path = os.path.join(agent_mem_dir, "TODO.md")
    _, exit_code = await sandbox.arun_command_in_container(
        f"test -f {shlex.quote(todo_path)}"
    )
    if exit_code != 0:
        todo_seed = (
            f"# TODO for {agent_name}\n\n"
            "- [ ] Capture the current task\n"
            "- [ ] Update progress as work proceeds\n"
        )
        await _write_text_to_main_sandbox(invocation_context, todo_path, todo_seed)

    # Create pinned.md (compaction-safe memory) if it doesn't exist.
    pinned_path = os.path.join(agent_mem_dir, "pinned.md")
    _, exit_code = await sandbox.arun_command_in_container(
        f"test -f {shlex.quote(pinned_path)}"
    )
    if exit_code != 0:
        await _write_text_to_main_sandbox(invocation_context, pinned_path, "")


async def _persist_traj_json(invocation_context, agent_mem_dir: str) -> None:
    """Persist full ADK session JSON into traj.json (sandbox path)."""
    session_json = invocation_context.session.model_dump_json(
        indent=2, exclude_none=True
    )
    traj_json_path = os.path.join(agent_mem_dir, "traj.json")
    await _write_text_to_main_sandbox(invocation_context, traj_json_path, session_json)


async def persist_traj_json_for_invocation(invocation_context) -> None:
    """Persist traj.json for the current invocation context (sandbox path)."""
    await _persist_traj_json(
        invocation_context, _compute_agent_mem_dir(invocation_context)
    )


# ---------------------------------------------------------------------------
# Host-side functions (always enabled, for Web UI sub-agent visualization and
# orchestration persistence — the two layers share the same nested tree).
# ---------------------------------------------------------------------------


def _host_instances_root(opensage_session_id: str) -> Path:
    """Return the top-level ``instances/`` dir for an OpenSage session."""
    return HOST_SESSION_ROOT / opensage_session_id / HOST_INSTANCES_SUBDIR


def compute_host_root_instance_dir(*, opensage_session_id: str, session_id: str) -> str:
    """Compute the host-side root agent directory path (pure sid, no name)."""
    return str(_host_instances_root(opensage_session_id) / session_id)


def _compute_host_child_instance_dir(
    parent_host_dir: str, *, child_session_id: str
) -> str:
    """Compute host-side child directory, nested under parent (pure sid)."""
    return str(Path(parent_host_dir) / child_session_id)


def _get_host_instance_dir(invocation_context) -> str | None:
    """Read host instance dir from session state. Returns None if not set."""
    state = getattr(invocation_context.session, "state", None)
    if isinstance(state, dict):
        val = state.get(HOST_INSTANCE_DIR_KEY)
        if isinstance(val, str) and val:
            return val
    return None


def _ensure_host_instance_dir(host_dir: str) -> None:
    """Create directory on host (marks agent as running before traj.json exists)."""
    try:
        Path(host_dir).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("Failed to create host instance dir: %s", e)


def _write_traj_json_sync(host_dir: str, session_json: str) -> None:
    path = Path(host_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / "traj.json").write_text(session_json, encoding="utf-8")


async def _persist_traj_json_to_host(invocation_context, host_dir: str) -> None:
    """Write traj.json to host filesystem (file I/O dispatched to a worker
    thread to avoid blocking the event loop)."""
    try:
        session_json = invocation_context.session.model_dump_json(
            indent=2, exclude_none=True
        )
        await asyncio.to_thread(_write_traj_json_sync, host_dir, session_json)
    except Exception as e:
        logger.warning("Failed to persist traj.json to host: %s", e)


def find_instance_dir(opensage_session_id: str, session_id: str) -> Path | None:
    """Recursively walk ``instances/`` and return the dir whose name == session_id.

    Since session_id is a UUID (globally unique), at most one match exists.
    Returns ``None`` if no such directory is found.
    """
    root = _host_instances_root(opensage_session_id)
    if not root.exists():
        return None
    # Direct child at top level is the common case (root instance).
    direct = root / session_id
    if direct.is_dir():
        return direct
    for current in root.rglob(session_id):
        if current.is_dir() and current.name == session_id:
            return current
    return None


def scan_host_instance_tree(opensage_session_id: str) -> dict:
    """Scan host ``instances/`` tree and return topology.

    Directory names are pure session_ids. agent_name is read from
    ``metadata.json``. Parent-child relationships come from directory nesting.

    Returns:
        {
            "root": {"name", "session_id", "dir", "has_traj", "status",
                     "query", "children": [...]} | None,
            "agents_flat": [
                {"name", "session_id", "dir", "parent_name",
                 "has_traj", "status", "query"},
                ...
            ]
        }

    Raises:
        ValueError: if more than one top-level instance is found.
        FileNotFoundError: if a subdirectory is missing metadata.json.
    """
    instances_root = _host_instances_root(opensage_session_id)
    if not instances_root.exists():
        return {"root": None, "agents_flat": []}

    def _read_agent_name(directory: Path) -> str:
        metadata_path = directory / "metadata.json"
        if not metadata_path.exists():
            raise FileNotFoundError(
                f"metadata.json missing in {directory} — "
                f"instance directory is corrupted or was not created via spawn"
            )
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        name = data.get("agent_name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"metadata.json in {directory} has no valid agent_name")
        return name

    def _extract_query(traj_path: Path) -> str:
        """Extract the first user message text from traj.json as the call query."""
        try:
            data = json.loads(traj_path.read_text(encoding="utf-8"))
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

    def _infer_status(traj_path: Path) -> str:
        """Infer status from the last event in traj.json."""
        if not traj_path.exists():
            return "running"
        try:
            data = json.loads(traj_path.read_text(encoding="utf-8"))
            events = data.get("events") or []
            if not events:
                return "running"

            last_event = events[-1] or {}
            if last_event.get("author") == "user":
                return "running"

            content = last_event.get("content") or {}
            parts = content.get("parts") or []
            if last_event.get("partial") is True:
                return "running"

            has_function_calls = any(
                isinstance(part, dict) and part.get("function_call") for part in parts
            )
            if has_function_calls:
                return "running"

            has_function_responses = any(
                isinstance(part, dict) and part.get("function_response")
                for part in parts
            )
            if has_function_responses:
                return "running"

            if (
                parts
                and isinstance(parts[-1], dict)
                and parts[-1].get("code_execution_result") is not None
            ):
                return "running"

            return "completed"
        except Exception:
            pass
        return "running"

    def _is_instance_dir(d: Path) -> bool:
        # An instance dir is identified by the presence of metadata.json.
        # Tool-output / scratch subdirs that lack metadata are skipped by
        # both the recursive walk and the top-level invariant check below.
        return (d / "metadata.json").exists()

    def _scan(directory: Path) -> dict | None:
        if not _is_instance_dir(directory):
            return None
        agent_name = _read_agent_name(directory)
        session_id = directory.name
        traj_path = directory / "traj.json"
        has_traj = traj_path.exists()
        status = _infer_status(traj_path)
        query = _extract_query(traj_path) if has_traj else ""
        children: list[dict] = []
        for child in sorted(directory.iterdir()):
            if not child.is_dir():
                continue
            scanned = _scan(child)
            if scanned is not None:
                children.append(scanned)
        return {
            "name": agent_name,
            "session_id": session_id,
            "dir": str(directory.relative_to(instances_root)),
            "has_traj": has_traj,
            "status": status,
            "query": query,
            "children": children,
        }

    # Filter non-instance siblings BEFORE the >1-root invariant check, so
    # the assertion still fires on the case it was designed for (truly two
    # instance roots) but tolerates unrelated scratch dirs at the top level.
    top_dirs = [
        d for d in instances_root.iterdir() if d.is_dir() and _is_instance_dir(d)
    ]
    if not top_dirs:
        return {"root": None, "agents_flat": []}
    if len(top_dirs) > 1:
        raise ValueError(
            f"instances/ has {len(top_dirs)} top-level instance dirs for "
            f"osid {opensage_session_id!r}; expected exactly 1 root instance. "
            f"Found: {[d.name for d in top_dirs]}"
        )

    root_node = _scan(top_dirs[0])

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
                "status": node.get("status", "running"),
            }
        )
        for child in node.get("children", []):
            _flatten(child, parent_name=node["name"])

    _flatten(root_node)
    return {"root": root_node, "agents_flat": agents_flat}
