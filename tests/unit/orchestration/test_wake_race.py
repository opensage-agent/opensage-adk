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
    inst.inbox.flush_cursor = AsyncMock()
    inst.runner = MagicMock()
    inst.runner.close = AsyncMock()
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
    mgr._maybe_unload = AsyncMock()  # avoid disk side effects

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

    # 1. Start wake handler. It flips state to RUNNING synchronously, then
    #    awaits has_pending() → blocks on `gate`.
    wake_task = asyncio.create_task(mgr._handle_wake_signal(sid))
    await has_pending_called.wait()
    assert instance.state == AgentInstanceState.RUNNING

    # 2. While wake holds the window, a concurrent _invoke_instance arrives.
    #    It must see RUNNING and return busy — NOT race past the check and
    #    start a second invocation.
    invoke_result = await mgr._invoke_instance(instance, "request", "sync", "caller")
    assert invoke_result["success"] is False
    assert invoke_result["error"] == "busy"
    assert invoke_result["session_id"] == sid

    # 3. Release the gate. Wake completes (no pending → rolls back to
    #    SLEEPING and calls _maybe_unload).
    gate.set()
    await wake_task
    assert instance.state == AgentInstanceState.SLEEPING
    assert instance._done_event.is_set()
    mgr._maybe_unload.assert_awaited_once_with(instance)


@pytest.mark.asyncio
async def test_burst_wake_signals_do_not_leak_ghost(monkeypatch, tmp_path):
    """N spurious wake signals after eviction must not leave a SLEEPING ghost.

    Folds [I5]: previously, signals 2..N would re-load an evicted instance
    and early-return without unloading.
    """
    mgr = _make_manager(monkeypatch, tmp_path)
    mgr._maybe_unload = AsyncMock()

    sid = "sid-ghost"
    instance = _make_sleeping_instance(sid)
    instance.inbox.has_pending = AsyncMock(return_value=False)

    # Simulate ensure_loaded returning the instance each time.
    mgr.ensure_loaded = AsyncMock(return_value=instance)

    # Fire 5 wake signals back to back.
    for _ in range(5):
        await mgr._handle_wake_signal(sid)

    # Each wake must have rolled back AND called _maybe_unload — no ghost.
    assert instance.state == AgentInstanceState.SLEEPING
    assert mgr._maybe_unload.await_count == 5
