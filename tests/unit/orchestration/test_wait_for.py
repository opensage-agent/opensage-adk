"""wait_for must distinguish "doesn't exist" / "spawn-not-loaded" /
"loaded but pending" / "stably idle" / "unloaded after run".

Replaces the old broken behavior where ``_instances.get(sid) is None`` was
treated as "already idle" — which silently misjudged unknown sids,
spawn-not-yet-loaded sids, and instances mid-load.
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

    def evict(sid):
        sessions.pop(sid, None)

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
    svc.evict = evict
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
    """A sid that has no on-disk instance dir raises KeyError. Old code
    silently returned ('already idle')."""
    with pytest.raises(KeyError):
        await manager.wait_for("definitely-not-a-real-sid")


@pytest.mark.asyncio
async def test_wait_for_idle_loaded_returns_quickly(manager):
    """Already loaded, SLEEPING, no task, empty inbox → return immediately."""
    sid = await manager.spawn("worker")
    inst = await manager.ensure_loaded(sid)
    assert inst.state == AgentInstanceState.SLEEPING
    assert inst._task is None

    # Should return within a tight bound.
    await asyncio.wait_for(manager.wait_for(sid), timeout=1.0)


@pytest.mark.asyncio
async def test_wait_for_force_loads_disk_only_instance(manager):
    """Spawn writes to disk but doesn't load into _instances. wait_for must
    force-load (so dispatcher / further work is even possible) instead of
    misreporting 'already idle'."""
    sid = await manager.spawn("worker")
    assert sid not in manager._instances

    await asyncio.wait_for(manager.wait_for(sid), timeout=1.0)

    # After wait_for, the instance was force-loaded.
    assert sid in manager._instances


@pytest.mark.asyncio
async def test_wait_for_returns_after_pending_messages_processed(manager):
    """Spawn + send a message: wait_for must wait until dispatcher has
    fully drained the inbox (or unloaded the instance), not return
    immediately just because state == SLEEPING flickers."""
    sid = await manager.spawn("worker")
    await manager.send_message(from_sid="caller", to_sid=sid, content="hello")

    # The runner is a no-op (yields nothing), so the invocation completes
    # quickly. wait_for should observe the drain.
    await asyncio.wait_for(manager.wait_for(sid), timeout=2.0)

    # After return: either unloaded (truly done) or stable SLEEPING with
    # empty inbox.
    inst = manager.get_instance(sid)
    if inst is not None:
        assert inst.state == AgentInstanceState.SLEEPING
        assert inst._task is None
        assert not await inst.inbox.has_pending()


@pytest.mark.asyncio
async def test_wait_for_timeout_raises(manager, monkeypatch):
    """If a never-completing invocation is in flight, timeout fires."""
    sid = await manager.spawn("worker")
    inst = await manager.ensure_loaded(sid)

    # Pin instance to RUNNING with a never-set _done_event so wait_for has
    # to block.
    inst.state = AgentInstanceState.RUNNING
    inst._done_event.clear()

    with pytest.raises(asyncio.TimeoutError):
        await manager.wait_for(sid, timeout=0.2)

    # Reset so fixture teardown isn't blocked.
    inst.state = AgentInstanceState.SLEEPING
    inst._done_event.set()
