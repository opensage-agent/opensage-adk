"""Tests for AgentManager.send_message: single-target and broadcast (to_sid='*')."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from opensage.orchestration.instance import AgentInstance
from opensage.orchestration.manager import AgentManager
from opensage.orchestration.persistence import (
    inbox_path,
    instance_dir,
    touch_inbox,
    write_metadata,
)
from opensage.orchestration.types import AgentInstanceState


@dataclass
class _StubOpenSageSession:
    opensage_session_id: str
    session_service: Any
    artifact_service: Any = None
    memory_service: Any = None
    credential_service: Any = None


def _make_manager(monkeypatch, tmp_path):
    from opensage.memory.file_based.short_term import session_files as sf

    monkeypatch.setattr(sf, "HOST_SESSION_ROOT", tmp_path, raising=True)
    svc = AsyncMock()
    op = _StubOpenSageSession(opensage_session_id="osid", session_service=svc)
    svc.opensage_session = op
    return AgentManager(op)  # type: ignore[arg-type]


def _seed_instance(mgr: AgentManager, sid: str) -> None:
    """Create on-disk dir + inbox AND register a stub in _instances."""
    d = instance_dir(mgr._opensage_session.opensage_session_id, sid)
    d.mkdir(parents=True, exist_ok=True)
    touch_inbox(d)
    write_metadata(d, {"session_id": sid, "agent_name": "stub"})
    # Also register in _instances so _all_known_sids finds it.
    from opensage.orchestration.inbox import Inbox

    inst = AgentInstance.__new__(AgentInstance)
    inst.session_id = sid
    agent = MagicMock()
    agent.description = "stub agent"
    inst.agent = agent
    inst.agent_name = "stub"
    inst.state = AgentInstanceState.SLEEPING
    inst.user_id = "user"
    inst.inbox = Inbox(inbox_path(d))
    inst.runner = MagicMock()
    inst._task = None
    inst._done_event = asyncio.Event()
    inst._done_event.set()
    mgr._instances[sid] = inst


def _read_inbox_lines(mgr: AgentManager, sid: str) -> list[dict]:
    p = inbox_path(instance_dir(mgr._opensage_session.opensage_session_id, sid))
    if not p.exists():
        return []
    return [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.mark.asyncio
async def test_send_to_single_writes_inbox_and_signals(monkeypatch, tmp_path):
    mgr = _make_manager(monkeypatch, tmp_path)
    _seed_instance(mgr, "sid-1")

    await mgr.send_message(from_sid="X", to_sid="sid-1", content="hello")

    msgs = _read_inbox_lines(mgr, "sid-1")
    assert len(msgs) == 1
    assert msgs[0]["from_sid"] == "X"
    assert msgs[0]["content"] == "hello"

    # The wake_queue got the sid
    assert mgr._wake_queue.qsize() == 1
    sid = await mgr._wake_queue.get()
    assert sid == "sid-1"


@pytest.mark.asyncio
async def test_send_to_unknown_raises(monkeypatch, tmp_path):
    mgr = _make_manager(monkeypatch, tmp_path)
    with pytest.raises(KeyError):
        await mgr.send_message(from_sid="X", to_sid="missing", content="hi")


@pytest.mark.asyncio
async def test_broadcast_pushes_to_all_known_except_sender(monkeypatch, tmp_path):
    mgr = _make_manager(monkeypatch, tmp_path)
    for sid in ["sid-A", "sid-B", "sid-C"]:
        _seed_instance(mgr, sid)

    # sid-A is the sender; expect B and C to receive, A to be skipped
    await mgr.send_message(from_sid="sid-A", to_sid="*", content="all hands")

    a_msgs = _read_inbox_lines(mgr, "sid-A")
    b_msgs = _read_inbox_lines(mgr, "sid-B")
    c_msgs = _read_inbox_lines(mgr, "sid-C")

    assert a_msgs == []  # sender skipped
    assert len(b_msgs) == 1 and b_msgs[0]["content"] == "all hands"
    assert len(c_msgs) == 1 and c_msgs[0]["content"] == "all hands"

    # 2 sids enqueued (B, C)
    assert mgr._wake_queue.qsize() == 2


@pytest.mark.asyncio
async def test_broadcast_with_no_targets_is_noop(monkeypatch, tmp_path):
    mgr = _make_manager(monkeypatch, tmp_path)
    _seed_instance(mgr, "only-self")

    await mgr.send_message(from_sid="only-self", to_sid="*", content="hello?")

    assert mgr._wake_queue.qsize() == 0
    assert _read_inbox_lines(mgr, "only-self") == []
