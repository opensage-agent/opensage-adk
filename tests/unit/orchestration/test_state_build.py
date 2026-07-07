"""Test the framework-state-key preserving spawn logic (build_child_state).

We bypass the full AgentManager.spawn (which needs a real OpenSageSession with
sandboxes) and call the private ``_build_child_state`` directly against a
stubbed manager.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.adk.sessions.session import Session

from opensage.orchestration.manager import AgentManager
from opensage.orchestration.types import FRAMEWORK_STATE_KEYS


@dataclass
class _StubOpenSageSession:
    opensage_session_id: str
    session_service: object
    artifact_service: object = None
    memory_service: object = None
    credential_service: object = None


@pytest.mark.asyncio
async def test_child_state_fresh_when_no_inherit(tmp_path, monkeypatch):
    """use_parent_history=False with no parent: state has only framework keys."""
    from opensage.memory.file_based.short_term import session_files as sf

    monkeypatch.setattr(sf, "HOST_SESSION_ROOT", tmp_path, raising=True)

    svc = AsyncMock()
    svc.get_session = AsyncMock(return_value=None)
    osid = "osid-123"
    op = _StubOpenSageSession(opensage_session_id=osid, session_service=svc)
    mgr = AgentManager(op)  # type: ignore[arg-type]

    fake_agent = MagicMock()
    fake_agent.name = "worker"

    state, events = await mgr._build_child_state(
        agent=fake_agent,
        new_sid="sid-new",
        parent_session_id=None,
        use_parent_history=False,
    )

    assert state["opensage_session_id"] == osid
    # Sandbox side keeps {name}__{sid}
    assert state["_mem_agent_dir"].endswith("worker__sid-new")
    # Host side is pure sid (no agent_name prefix)
    assert state["_host_instance_dir"].endswith("/instances/sid-new")
    # No business state
    assert set(state.keys()) == FRAMEWORK_STATE_KEYS
    assert events == []


@pytest.mark.asyncio
async def test_child_state_inherits_business_keys(tmp_path, monkeypatch):
    """use_parent_history=True: non-framework parent state is deep-copied; paths still fresh."""
    from opensage.memory.file_based.short_term import session_files as sf

    monkeypatch.setattr(sf, "HOST_SESSION_ROOT", tmp_path, raising=True)

    parent_session = Session(
        id="sid-parent",
        app_name="x",
        user_id="user",
        state={
            "opensage_session_id": "osid-123",
            "_mem_agent_dir": "/mem/short_term/root__sid-parent",
            "_host_instance_dir": str(
                tmp_path / "osid-123" / "instances" / "sid-parent"
            ),
            "task_finished": False,
            "alias": "foo",
            "_doom_loop_history": [1, 2, 3],
        },
        events=[],
    )
    svc = AsyncMock()
    svc.get_session = AsyncMock(return_value=parent_session)
    osid = "osid-123"
    op = _StubOpenSageSession(opensage_session_id=osid, session_service=svc)
    mgr = AgentManager(op)  # type: ignore[arg-type]

    fake_agent = MagicMock()
    fake_agent.name = "child"

    state, events = await mgr._build_child_state(
        agent=fake_agent,
        new_sid="sid-child",
        parent_session_id="sid-parent",
        use_parent_history=True,
    )

    # Framework keys always fresh (derived from parent paths)
    assert state["opensage_session_id"] == osid
    # Sandbox side still nests with {name}__{sid}
    assert (
        state["_mem_agent_dir"] == "/mem/short_term/root__sid-parent/child__sid-child"
    )
    # Host side is pure sid nested
    assert state["_host_instance_dir"].endswith("instances/sid-parent/sid-child")

    # Business/plugin state inherited
    assert state["task_finished"] is False
    assert state["alias"] == "foo"
    assert state["_doom_loop_history"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_child_state_no_inherit_with_parent(tmp_path, monkeypatch):
    """use_parent_history=False with parent: paths still nest; no business state."""
    from opensage.memory.file_based.short_term import session_files as sf

    monkeypatch.setattr(sf, "HOST_SESSION_ROOT", tmp_path, raising=True)

    parent_session = Session(
        id="sid-parent",
        app_name="x",
        user_id="user",
        state={
            "opensage_session_id": "osid-123",
            "_mem_agent_dir": "/mem/short_term/root__sid-parent",
            "_host_instance_dir": str(
                tmp_path / "osid-123" / "instances" / "sid-parent"
            ),
            "task_finished": True,
            "alias": "leak",
        },
        events=[],
    )
    svc = AsyncMock()
    svc.get_session = AsyncMock(return_value=parent_session)
    op = _StubOpenSageSession(opensage_session_id="osid-123", session_service=svc)
    mgr = AgentManager(op)  # type: ignore[arg-type]

    fake_agent = MagicMock()
    fake_agent.name = "child"

    state, events = await mgr._build_child_state(
        agent=fake_agent,
        new_sid="sid-child",
        parent_session_id="sid-parent",
        use_parent_history=False,
    )

    # Framework keys nested under parent
    assert (
        state["_mem_agent_dir"] == "/mem/short_term/root__sid-parent/child__sid-child"
    )
    assert state["_host_instance_dir"].endswith("instances/sid-parent/sid-child")

    # No business state leaked
    assert "task_finished" not in state
    assert "alias" not in state
    assert set(state.keys()) == FRAMEWORK_STATE_KEYS
    assert events == []
