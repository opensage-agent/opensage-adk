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
