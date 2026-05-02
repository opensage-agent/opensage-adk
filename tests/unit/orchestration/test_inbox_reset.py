"""AgentManager.start() resets every persisted inbox on disk.

Peer messages do NOT survive across process restarts. ``manager.start``
truncates every ``inbox.jsonl`` and removes every ``inbox.cursor`` under
the session's ``instances/`` tree. Within-process unload+reload still uses
``_maybe_unload``'s flush_cursor for cursor continuity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from opensage.orchestration.manager import AgentManager


@dataclass
class _StubOpenSageSession:
    opensage_session_id: str
    session_service: object
    artifact_service: object = None
    memory_service: object = None
    credential_service: object = None


@pytest_asyncio.fixture
async def manager(tmp_path, monkeypatch):
    """An AgentManager whose persistence root is redirected to tmp_path."""
    from opensage.memory.file_based.short_term import session_files as sf

    monkeypatch.setattr(sf, "HOST_SESSION_ROOT", tmp_path, raising=True)

    svc = AsyncMock()
    op = _StubOpenSageSession(opensage_session_id="osid-test", session_service=svc)
    mgr = AgentManager(op)  # type: ignore[arg-type]

    yield mgr

    if mgr._dispatcher_task is not None:
        await mgr.shutdown()


def _seed_instance(root: Path, sid: str, jsonl_lines: list[str], cursor_value: int):
    inst_dir = root / sid
    inst_dir.mkdir(parents=True, exist_ok=True)
    (inst_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (inst_dir / "inbox.jsonl").write_text(
        "\n".join(jsonl_lines) + "\n", encoding="utf-8"
    )
    (inst_dir / "inbox.cursor").write_text(str(cursor_value), encoding="utf-8")
    return inst_dir


@pytest.mark.asyncio
async def test_start_truncates_existing_inboxes(manager, tmp_path):
    """Pre-existing inbox.jsonl content is wiped, .cursor is deleted."""
    instances_dir = tmp_path / "osid-test" / "instances"
    inst_dir = _seed_instance(
        instances_dir,
        sid="aaa",
        jsonl_lines=['{"from_sid":"x","to_sid":"aaa","content":"hi","kind":"text"}'],
        cursor_value=0,
    )

    inbox_file = inst_dir / "inbox.jsonl"
    cursor_file = inst_dir / "inbox.cursor"
    assert inbox_file.read_text() != ""
    assert cursor_file.exists()

    await manager.start()

    assert inbox_file.read_text() == ""
    assert not cursor_file.exists()


@pytest.mark.asyncio
async def test_start_resets_nested_instances(manager, tmp_path):
    """Inboxes under nested (child) instance dirs are also reset."""
    instances_dir = tmp_path / "osid-test" / "instances"
    parent_inst = _seed_instance(
        instances_dir,
        sid="parent",
        jsonl_lines=['{"from_sid":"x","to_sid":"parent","content":"a","kind":"text"}'],
        cursor_value=10,
    )
    # Child instance is nested under parent (matches AgentManager's spawn layout).
    child_dir = parent_inst / "child"
    child_dir.mkdir()
    (child_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (child_dir / "inbox.jsonl").write_text(
        '{"from_sid":"y","to_sid":"child","content":"b","kind":"text"}\n',
        encoding="utf-8",
    )
    (child_dir / "inbox.cursor").write_text("5", encoding="utf-8")

    await manager.start()

    assert (parent_inst / "inbox.jsonl").read_text() == ""
    assert not (parent_inst / "inbox.cursor").exists()
    assert (child_dir / "inbox.jsonl").read_text() == ""
    assert not (child_dir / "inbox.cursor").exists()


@pytest.mark.asyncio
async def test_start_no_op_when_no_instances_dir(manager, tmp_path):
    """No instances dir on disk: start succeeds, doesn't create anything."""
    # Don't seed anything.
    await manager.start()
    # Nothing to assert beyond "didn't crash".
    assert manager._dispatcher_task is not None


@pytest.mark.asyncio
async def test_start_preserves_other_files(manager, tmp_path):
    """metadata.json, traj.json etc are not touched."""
    instances_dir = tmp_path / "osid-test" / "instances"
    inst_dir = _seed_instance(
        instances_dir,
        sid="zzz",
        jsonl_lines=['{"from_sid":"x","to_sid":"zzz","content":"hi","kind":"text"}'],
        cursor_value=0,
    )
    (inst_dir / "traj.json").write_text('{"events":[]}', encoding="utf-8")

    await manager.start()

    assert (inst_dir / "metadata.json").exists()
    assert (inst_dir / "traj.json").read_text() == '{"events":[]}'
