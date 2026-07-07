"""Test the C3 atomic transition in `_handle_wake_signal`.

Uses an explicit `asyncio.Event` to deterministically hold the race window
open while a concurrent `_invoke_instance` runs. `asyncio.sleep` would be
flaky — scheduling order isn't guaranteed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from opensage.orchestration.instance import AgentInstance
from opensage.orchestration.manager import AgentManager
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


def _make_sleeping_instance(sid: str) -> AgentInstance:
    inst = AgentInstance.__new__(AgentInstance)
    inst.session_id = sid
    inst.agent = MagicMock()
    inst.agent_name = "worker"
    inst.state = AgentInstanceState.SLEEPING
    inst.user_id = "user"
    inst.inbox = MagicMock()
    inst.inbox.pop_all = AsyncMock(return_value=[])
    inst.runner = MagicMock()
    inst._task = None
    inst._done_event = asyncio.Event()
    inst._done_event.set()
    return inst


@pytest.mark.asyncio
async def test_wake_signal_no_double_invocation_when_invoke_races(
    monkeypatch, tmp_path
):
    """Wake handler holds RUNNING window; concurrent _invoke gets busy."""
    mgr = _make_manager(monkeypatch, tmp_path)

    sid = "sid-race"
    instance = _make_sleeping_instance(sid)
    mgr._instances[sid] = instance

    # Gate has_pending so the test controls when the wake handler advances.
    gate = asyncio.Event()
    has_pending_called = asyncio.Event()

    async def gated_has_pending():
        has_pending_called.set()
        await gate.wait()
        return False  # no real work, will roll back

    monkeypatch.setattr(instance.inbox, "has_pending", gated_has_pending)

    wake_task = asyncio.create_task(mgr._handle_wake_signal(sid))
    await has_pending_called.wait()
    assert instance.state == AgentInstanceState.RUNNING

    invoke_result = await mgr._invoke_instance(instance, "request", "sync", "caller")
    assert invoke_result["success"] is False
    assert invoke_result["error"] == "busy"
    assert invoke_result["session_id"] == sid

    gate.set()
    await wake_task
    assert instance.state == AgentInstanceState.SLEEPING
    assert instance._done_event.is_set()


@pytest.mark.asyncio
async def test_burst_wake_signals_do_not_double_invoke(monkeypatch, tmp_path):
    """N burst wake signals for a SLEEPING instance with no pending messages
    all roll back to SLEEPING without error."""
    mgr = _make_manager(monkeypatch, tmp_path)

    sid = "sid-burst"
    instance = _make_sleeping_instance(sid)
    instance.inbox.has_pending = AsyncMock(return_value=False)
    mgr._instances[sid] = instance

    for _ in range(5):
        await mgr._handle_wake_signal(sid)

    assert instance.state == AgentInstanceState.SLEEPING
    assert instance._done_event.is_set()
