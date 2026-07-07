"""wait_for must distinguish "not in memory" / "idle" / "running" / "pending".

All instances live in memory for their entire lifetime. wait_for raises
KeyError for unknown sids and blocks until idle for known ones.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from opensage.orchestration.manager import AgentManager
from opensage.orchestration.types import AgentInstanceState


@dataclass
class _StubOpenSageSession:
    opensage_session_id: str
    session_service: Any
    artifact_service: Any = None
    memory_service: Any = None
    credential_service: Any = None


class _RecordingRunner:
    def __init__(self, *args, **kwargs):
        pass

    async def run_async(self, *, user_id, session_id, new_message, run_config=None):
        if False:
            yield None
        return

    async def close(self):
        pass


def _make_session_service():
    svc = AsyncMock()
    sessions: dict[str, Any] = {}

    def inject_session(sess):
        sessions[sess.id] = sess

    async def get_session(*, app_name, user_id, session_id, config=None):
        return sessions.get(session_id)

    async def create_session(*, app_name, user_id, state=None, session_id=None):
        from google.adk.sessions.session import Session

        sess = Session(
            id=session_id,
            app_name=app_name,
            user_id=user_id,
            state=state or {},
            events=[],
        )
        sessions[session_id] = sess
        return sess

    async def append_event(session, event):
        session.events.append(event)

    svc.inject_session = inject_session
    svc.get_session = get_session
    svc.create_session = create_session
    svc.append_event = append_event
    svc.opensage_session = None
    return svc


@pytest_asyncio.fixture
async def manager(tmp_path, monkeypatch):
    from opensage.memory.file_based.short_term import session_files as sf

    monkeypatch.setattr(sf, "HOST_SESSION_ROOT", tmp_path, raising=True)

    osid = "osid-test"
    svc = _make_session_service()
    op = _StubOpenSageSession(opensage_session_id=osid, session_service=svc)
    svc.opensage_session = op

    mgr = AgentManager(op)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "opensage.orchestration.manager.Runner",
        lambda *a, **kw: _RecordingRunner(),
        raising=True,
    )

    fake_agent = MagicMock()
    fake_agent.name = "worker"
    fake_agent.model_copy = MagicMock(return_value=fake_agent)
    mgr.register_agent("worker", fake_agent)

    await mgr.start()
    try:
        yield mgr
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_wait_for_unknown_sid_raises(manager):
    """A sid not in memory raises KeyError."""
    with pytest.raises(KeyError):
        await manager.wait_for("definitely-not-a-real-sid")


@pytest.mark.asyncio
async def test_wait_for_idle_loaded_returns_quickly(manager):
    """Already loaded, SLEEPING, no task, empty inbox -> return immediately."""
    sid = await manager.spawn("worker")
    inst = manager.ensure_loaded(sid)
    assert inst.state == AgentInstanceState.SLEEPING
    assert inst._task is None

    await asyncio.wait_for(manager.wait_for(sid), timeout=1.0)


@pytest.mark.asyncio
async def test_wait_for_returns_after_pending_messages_processed(manager):
    """Spawn + send a message: wait_for must wait until dispatcher has
    fully drained the inbox, not return immediately."""
    sid = await manager.spawn("worker")
    await manager.send_message(from_sid="caller", to_sid=sid, content="hello")

    await asyncio.wait_for(manager.wait_for(sid), timeout=2.0)

    inst = manager.get_instance(sid)
    assert inst is not None
    assert inst.state == AgentInstanceState.SLEEPING
    assert inst._task is None
    assert not await inst.inbox.has_pending()


@pytest.mark.asyncio
async def test_wait_for_timeout_raises(manager):
    """If a never-completing invocation is in flight, timeout fires."""
    sid = await manager.spawn("worker")
    inst = manager.ensure_loaded(sid)

    inst.state = AgentInstanceState.RUNNING
    inst._done_event.clear()

    with pytest.raises(asyncio.TimeoutError):
        await manager.wait_for(sid, timeout=0.2)

    inst.state = AgentInstanceState.SLEEPING
    inst._done_event.set()
