"""Tests for the directed-invoke (call_subagent / continue_agent_instance)
plumbing on AgentManager._invoke_instance, focused on the busy semantics."""

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


def _make_instance(state: AgentInstanceState) -> AgentInstance:
    """Build an AgentInstance with mocked inbox + runner."""
    inst = AgentInstance.__new__(AgentInstance)
    inst.session_id = "sid-test"
    inst.agent = MagicMock()
    inst.agent_name = "worker"
    inst.state = state
    inst.user_id = "user"

    inbox = MagicMock()
    inbox.pop_all = AsyncMock(return_value=[])
    inbox.has_pending = AsyncMock(return_value=False)
    inbox.flush_cursor = AsyncMock()
    inst.inbox = inbox

    runner = MagicMock()

    async def _run(*args, **kwargs):
        if False:
            yield None

    runner.run_async = MagicMock(side_effect=_run)
    runner.close = AsyncMock()
    inst.runner = runner

    inst._task = None
    inst._done_event = asyncio.Event()
    inst._done_event.set()
    return inst


@pytest.mark.asyncio
async def test_invoke_returns_busy_when_running(monkeypatch, tmp_path):
    """When the instance is already RUNNING, _invoke_instance returns busy."""
    mgr = _make_manager(monkeypatch, tmp_path)
    inst = _make_instance(AgentInstanceState.RUNNING)

    result = await mgr._invoke_instance(inst, "request", "sync", "caller")
    assert result["success"] is False
    assert result["error"] == "busy"
    assert result["session_id"] == inst.session_id


@pytest.mark.asyncio
async def test_invoke_sync_returns_result_when_sleeping(monkeypatch, tmp_path):
    """SLEEPING -> sync invocation returns final text and transitions back."""
    mgr = _make_manager(monkeypatch, tmp_path)
    inst = _make_instance(AgentInstanceState.SLEEPING)

    result = await mgr._invoke_instance(inst, "request", "sync", "caller")
    assert result["success"] is True
    assert result["session_id"] == inst.session_id
    # Empty event stream → empty result
    assert "result" in result
    # State returned to SLEEPING after invocation
    assert inst.state == AgentInstanceState.SLEEPING


@pytest.mark.asyncio
async def test_invoke_async_starts_task_and_returns_immediately(monkeypatch, tmp_path):
    """SLEEPING -> async invocation returns running status without blocking."""
    mgr = _make_manager(monkeypatch, tmp_path)
    inst = _make_instance(AgentInstanceState.SLEEPING)

    result = await mgr._invoke_instance(inst, "request", "async", "caller")
    assert result["success"] is True
    assert result["status"] == "running"
    # State should already be RUNNING (the background task may or may not have
    # finished by the time we get here, but it was set RUNNING synchronously)
    # NB: with our trivial Runner the task may complete immediately and flip
    # back to SLEEPING; the contract is just "no busy error".
    assert "error" not in result


@pytest.mark.asyncio
async def test_invoke_sync_reports_runner_exception_as_failure(monkeypatch, tmp_path):
    """SLEEPING -> runner raises -> caller sees success=False with details."""
    mgr = _make_manager(monkeypatch, tmp_path)
    inst = _make_instance(AgentInstanceState.SLEEPING)

    async def _boom(*args, **kwargs):
        raise RuntimeError("boom")
        yield None  # make it an async generator

    inst.runner.run_async = MagicMock(side_effect=_boom)

    result = await mgr._invoke_instance(inst, "request", "sync", "caller")
    assert result["success"] is False
    assert result["error"] == "invocation_error"
    assert "RuntimeError: boom" in result["exception"]
    assert result["session_id"] == inst.session_id
    # State must have rolled back so the instance is reusable.
    assert inst.state == AgentInstanceState.SLEEPING
