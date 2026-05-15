"""Helpers for reading/writing instance persistence files on disk.

Each instance lives at a nested path under ``instances/`` — parent-child is
expressed by directory nesting, not by metadata alone:

    ~/.local/opensage/sessions/{osid}/instances/
        {root_sid}/
            ├── traj.json
            ├── inbox.jsonl
            ├── inbox.cursor
            ├── metadata.json
            └── {child_sid}/
                ├── traj.json
                ├── inbox.jsonl
                ├── inbox.cursor
                └── metadata.json

To locate a dir from a bare sid we recursively walk (``find_instance_dir``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from google.adk.sessions.session import Session

logger = logging.getLogger("opensage." + __name__)


def instances_root(opensage_session_id: str) -> Path:
    from opensage.memory.file_based.short_term.session_files import (
        HOST_INSTANCES_SUBDIR,
        HOST_SESSION_ROOT,
    )

    return HOST_SESSION_ROOT / opensage_session_id / HOST_INSTANCES_SUBDIR


def instance_dir(opensage_session_id: str, session_id: str) -> Path:
    """Return the on-disk dir for an instance, locating it by recursive walk.

    If no existing dir is found, returns the path where a top-level root
    instance *would* live (``instances/{session_id}/``). Callers that are
    creating a new instance should prefer explicit placement (e.g. under a
    parent's host dir) via ``child_instance_dir``.
    """
    from opensage.memory.file_based.short_term.session_files import find_instance_dir

    found = find_instance_dir(opensage_session_id, session_id)
    if found is not None:
        return found
    # Default (for new top-level instances / fresh creation)
    return instances_root(opensage_session_id) / session_id


def child_instance_dir(parent_dir: Path, child_session_id: str) -> Path:
    """Return the on-disk dir for a new child instance nested under ``parent_dir``."""
    return parent_dir / child_session_id


def agents_root(opensage_session_id: str) -> Path:
    from opensage.memory.file_based.short_term.session_files import HOST_SESSION_ROOT

    return HOST_SESSION_ROOT / opensage_session_id / "agents"


def agent_dir(opensage_session_id: str, agent_name: str) -> Path:
    return agents_root(opensage_session_id) / agent_name


def write_metadata(dir_: Path, metadata: dict[str, Any]) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def read_metadata(dir_: Path) -> dict[str, Any]:
    return json.loads((dir_ / "metadata.json").read_text(encoding="utf-8"))


def save_adk_session(dir_: Path, session: "Session") -> None:
    """Write the ADK Session object to ``traj.json``.

    Merged with the BaseAgent.run_async patch's traj.json, so both layers share
    a single source of truth.
    """
    dir_.mkdir(parents=True, exist_ok=True)
    text = session.model_dump_json(indent=2, exclude_none=True)
    (dir_ / "traj.json").write_text(text, encoding="utf-8")


def load_adk_session(dir_: Path) -> "Session":
    """Read ``traj.json`` back into an ADK Session object."""
    from google.adk.sessions.session import Session

    text = (dir_ / "traj.json").read_text(encoding="utf-8")
    return Session.model_validate_json(text)


def touch_inbox(dir_: Path) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "inbox.jsonl").touch()


def inbox_path(dir_: Path) -> Path:
    return dir_ / "inbox.jsonl"


def list_instance_sids(opensage_session_id: str) -> list[str]:
    """Recursively walk ``instances/`` and return every instance's session_id.

    Directory name == session_id (pure sid, no prefix).
    """
    root = instances_root(opensage_session_id)
    if not root.exists():
        return []
    sids: list[str] = []
    for path in root.rglob("*"):
        if path.is_dir() and (path / "metadata.json").exists():
            sids.append(path.name)
    return sorted(sids)


def list_agent_names_on_disk(opensage_session_id: str) -> list[str]:
    root = agents_root(opensage_session_id)
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def save_agent_definition(
    opensage_session_id: str, agent_name: str, definition: dict[str, Any]
) -> None:
    d = agent_dir(opensage_session_id, agent_name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "definition.json").write_text(
        json.dumps(definition, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_agent_definition(
    opensage_session_id: str, agent_name: str
) -> dict[str, Any] | None:
    p = agent_dir(opensage_session_id, agent_name) / "definition.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to load agent definition: %s", p)
        return None


# ------------------------------------------------------------------
# Session snapshot (shared by CLI web and benchmark runner)
# ------------------------------------------------------------------


def _drop_unmatched_function_call_events(events: list) -> list:
    matched_response_ids: set[str] = set()
    for event in events:
        parts = getattr(getattr(event, "content", None), "parts", None) or []
        for part in parts:
            fr = getattr(part, "function_response", None)
            if fr and getattr(fr, "id", None):
                matched_response_ids.add(fr.id)

    sanitized = []
    for event in events:
        parts = getattr(getattr(event, "content", None), "parts", None) or []
        if not parts:
            sanitized.append(event)
            continue
        kept = []
        removed_any = False
        for part in parts:
            fc = getattr(part, "function_call", None)
            if fc and getattr(fc, "id", None) and fc.id not in matched_response_ids:
                removed_any = True
                continue
            kept.append(part)
        if not removed_any:
            sanitized.append(event)
        elif kept:
            e = event.model_copy(deep=True)
            e.content.parts = kept
            sanitized.append(e)
    return sanitized


def sanitize_adk_session(session: "Session", *, copy: bool = True) -> "Session":
    target = session.model_copy(deep=True) if copy else session
    target.events = _drop_unmatched_function_call_events(target.events or [])
    if target.events:
        ts = getattr(target.events[-1], "timestamp", None)
        if ts is not None:
            target.last_update_time = ts
    return target


def collect_sandbox_runtime_metadata(opensage_session) -> dict:
    backend = (
        getattr(getattr(opensage_session, "config", None), "sandbox", None)
        and opensage_session.config.sandbox.backend
    ) or "native"
    sandboxes = {}
    for sandbox_type, sandbox in opensage_session.sandboxes.list_sandboxes().items():
        entry = {"backend": backend}
        for attr in ("container_id", "pod_name", "container_name"):
            val = getattr(sandbox, attr, None)
            if val:
                entry[attr] = val
        sandboxes[sandbox_type] = entry
    return {"backend": backend, "sandboxes": sandboxes}


def persist_session_snapshot(
    *,
    opensage_session,
    agent_dir: str,
    app_name: str | None = None,
    user_id: str = "user",
    root_agent_name: str | None = None,
) -> Path:
    """Write session snapshot (metadata + adk_session + config) to the session dir.

    Works for both web and evaluation sessions — writes into the same
    ``~/.local/opensage/sessions/{session_id}/`` directory that already
    holds ``instances/`` and ``agents/``.
    """
    import time

    from google.adk.sessions.session import Session

    from opensage.memory.file_based.short_term.session_files import find_instance_dir
    from opensage.utils.agent_utils import sanitize_agent_name

    session_id = opensage_session.opensage_session_id
    store_dir = instances_root(session_id).parent
    store_dir.mkdir(parents=True, exist_ok=True)

    inst_dir = find_instance_dir(session_id, session_id)
    if inst_dir is None:
        raise RuntimeError(
            f"Cannot persist session: instance dir not found ({session_id})"
        )
    traj_path = inst_dir / "traj.json"
    if not traj_path.exists():
        raise RuntimeError(f"Cannot persist session: traj.json not found in {inst_dir}")
    adk_session = Session.model_validate_json(traj_path.read_text(encoding="utf-8"))
    snapshot = sanitize_adk_session(adk_session, copy=False)

    (store_dir / "adk_session.json").write_text(
        snapshot.model_dump_json(indent=2, exclude_none=True), encoding="utf-8"
    )

    resolved_config_path = store_dir / "resolved_config.toml"
    opensage_session.config.save_to_toml(str(resolved_config_path))

    agent_name = sanitize_agent_name(root_agent_name or "agent")
    if not app_name:
        app_name = getattr(opensage_session.agent_manager, "app_name", "app")
    metadata = {
        "session_id": session_id,
        "agent_name": agent_name,
        "agent_dir": str(Path(agent_dir).expanduser().resolve()),
        "app_name": app_name,
        "user_id": user_id,
        "saved_at_unix": int(time.time()),
        "resolved_config_file": resolved_config_path.name,
        "runtime": collect_sandbox_runtime_metadata(opensage_session),
    }
    (store_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )

    logger.info("Persisted session snapshot to %s", store_dir)
    return store_dir
