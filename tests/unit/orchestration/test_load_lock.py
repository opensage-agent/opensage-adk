"""ensure_loaded uses a per-sid asyncio.Lock to serialize concurrent loads.

Without the lock, two coroutines hitting ensure_loaded(sid) at the same
time before the instance lands in ``_instances`` would each build their
own ``AgentInstance`` (with its own Runner / agent clone), and the late
writer would clobber the early one in ``_instances``. With the fix all
concurrent callers receive the same single instance.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from opensage.orchestration.manager import AgentManager


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
async def test_concurrent_ensure_loaded_returns_same_instance(manager):
    """Many concurrent ensure_loaded(sid) calls converge on a single
    AgentInstance. Without the per-sid Lock, the late writers would
    clobber earlier writers in _instances and observers would see
    different instance objects."""
    sid = await manager.spawn("worker")
    # spawn does not add to _instances; confirm.
    assert sid not in manager._instances

    n = 16
    instances = await asyncio.gather(*[manager.ensure_loaded(sid) for _ in range(n)])

    first = instances[0]
    for inst in instances:
        assert inst is first, (
            "concurrent ensure_loaded produced multiple AgentInstance objects"
        )

    # The single instance is the one in the dict.
    assert manager._instances[sid] is first


@pytest.mark.asyncio
async def test_ensure_loaded_idempotent_after_load(manager):
    """Calling ensure_loaded again after the first load is a fast-path
    return — same instance, no new build."""
    sid = await manager.spawn("worker")
    inst1 = await manager.ensure_loaded(sid)
    inst2 = await manager.ensure_loaded(sid)
    inst3 = await manager.ensure_loaded(sid)
    assert inst1 is inst2 is inst3


@pytest.mark.asyncio
async def test_ensure_loaded_raises_for_unknown_sid(manager):
    """ensure_loaded for a sid with no instance dir raises KeyError."""
    with pytest.raises(KeyError):
        await manager.ensure_loaded("never-spawned")
