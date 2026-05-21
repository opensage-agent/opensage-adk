"""Tests for the directed-invoke (call_subagent / continue_agent_instance)
plumbing on AgentManager._invoke_instance, focused on the busy semantics."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.genai import types

from opensage.orchestration.instance import AgentInstance
from opensage.orchestration.manager import AgentManager
from opensage.orchestration.persistence import instances_root
from opensage.orchestration.types import AgentInstanceState, Message


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


def test_active_background_tasks_include_descendant_owners(monkeypatch, tmp_path):
    mgr = _make_manager(monkeypatch, tmp_path)
    root = _make_instance(AgentInstanceState.SLEEPING)
    root.session_id = "root"
    root.parent_session_id = None
    child = _make_instance(AgentInstanceState.SLEEPING)
    child.session_id = "child"
    child.parent_session_id = "root"
    grandchild = _make_instance(AgentInstanceState.SLEEPING)
    grandchild.session_id = "grandchild"
    grandchild.parent_session_id = "child"
    other = _make_instance(AgentInstanceState.SLEEPING)
    other.session_id = "other"
    other.parent_session_id = None
    mgr._instances = {
        "root": root,
        "child": child,
        "grandchild": grandchild,
        "other": other,
    }

    class _TaskManager:
        def get_active_watcher_task_ids(self, owner_matches):
            owners = {
                "root-task": "root",
                "child-task": "child",
                "grandchild-task": "grandchild",
                "other-task": "other",
                "unknown-task": None,
            }
            return [
                task_id
                for task_id, owner_sid in owners.items()
                if owner_matches(owner_sid)
            ]

    mgr._opensage_session.bash_tasks = _TaskManager()

    assert mgr.get_active_background_tasks("root") == [
        "root-task",
        "child-task",
        "grandchild-task",
    ]
    assert mgr.get_active_background_tasks("child") == [
        "child-task",
        "grandchild-task",
    ]


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


@pytest.mark.asyncio
async def test_async_invocation_processes_child_results_before_reply(
    monkeypatch, tmp_path
):
    mgr = _make_manager(monkeypatch, tmp_path)
    (instances_root("osid") / "caller").mkdir(parents=True)
    child = _make_instance(AgentInstanceState.SLEEPING)
    child.session_id = "child"
    child.parent_session_id = "caller"
    child_outputs: list[str] = []
    child_requests: list[str] = []
    sent_messages: list[dict[str, Any]] = []
    inbox_messages: list[Message] = []

    async def _has_pending() -> bool:
        return bool(inbox_messages)

    async def _pop_all() -> list[Message]:
        msgs = list(inbox_messages)
        inbox_messages.clear()
        return msgs

    child.inbox.has_pending = _has_pending
    child.inbox.pop_all = _pop_all

    class _Event:
        author = "child"

        def __init__(self, text: str) -> None:
            self.content = types.Content(
                role="model",
                parts=[types.Part.from_text(text=text)],
            )

        def get_function_calls(self) -> list[Any]:
            return []

    async def _run_child(*, new_message, **kwargs):
        text = "".join(part.text or "" for part in new_message.parts or [])
        child_requests.append(text)
        if len(child_requests) == 1:
            child_outputs.append("waiting")
            yield _Event("waiting")
        else:
            child_outputs.append("done")
            yield _Event("done")

    child.runner.run_async = _run_child

    wait_calls = 0

    async def _wait_for_children(parent_sid: str, timeout=None):
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            msg = Message(
                from_sid="grandchild",
                to_sid="child",
                content="flag submitted",
                kind="result",
            )
            inbox_messages.append(msg)
            return ["grandchild"]
        return []

    async def _send_message(**kwargs):
        sent_messages.append(kwargs)

    monkeypatch.setattr(mgr, "wait_for_children", _wait_for_children)
    monkeypatch.setattr(mgr, "send_message", _send_message)

    await mgr._invoke_instance(child, "start", "async", "caller")
    await asyncio.wait_for(child._task, timeout=1.0)

    assert wait_calls == 2
    assert child_outputs == ["waiting", "done"]
    assert any("flag submitted" in request for request in child_requests)
    assert sent_messages == [
        {
            "from_sid": "child",
            "to_sid": "caller",
            "content": "done",
            "kind": "result",
            "error": None,
        }
    ]


@pytest.mark.asyncio
async def test_async_invocation_waits_for_background_tasks_before_reply(
    monkeypatch, tmp_path
):
    mgr = _make_manager(monkeypatch, tmp_path)
    (instances_root("osid") / "caller").mkdir(parents=True)
    child = _make_instance(AgentInstanceState.SLEEPING)
    child.session_id = "child"
    child.parent_session_id = "caller"
    child_outputs: list[str] = []
    child_requests: list[str] = []
    sent_messages: list[dict[str, Any]] = []
    inbox_messages: list[Message] = []

    async def _has_pending() -> bool:
        return bool(inbox_messages)

    async def _pop_all() -> list[Message]:
        msgs = list(inbox_messages)
        inbox_messages.clear()
        return msgs

    child.inbox.has_pending = _has_pending
    child.inbox.pop_all = _pop_all

    class _Event:
        author = "child"

        def __init__(self, text: str) -> None:
            self.content = types.Content(
                role="model",
                parts=[types.Part.from_text(text=text)],
            )

        def get_function_calls(self) -> list[Any]:
            return []

    async def _run_child(*, new_message, **kwargs):
        text = "".join(part.text or "" for part in new_message.parts or [])
        child_requests.append(text)
        if len(child_requests) == 1:
            child_outputs.append("waiting")
            yield _Event("waiting")
        else:
            child_outputs.append("done")
            yield _Event("done")

    child.runner.run_async = _run_child

    class _TaskManager:
        def __init__(self) -> None:
            self.active = ["task-1"]
            self.wait_calls = 0

        def get_active_watcher_task_ids(self, owner_matches):
            if self.active and owner_matches("child"):
                return list(self.active)
            return []

        async def wait_for_watcher_tasks(self, task_ids, timeout=None):
            self.wait_calls += 1
            self.active = []
            inbox_messages.append(
                Message(
                    from_sid="child",
                    to_sid="child",
                    content="background output ready",
                    kind="background_task_result",
                )
            )

    task_manager = _TaskManager()
    mgr._opensage_session.bash_tasks = task_manager

    async def _send_message(**kwargs):
        sent_messages.append(kwargs)

    monkeypatch.setattr(mgr, "send_message", _send_message)

    await mgr._invoke_instance(child, "start", "async", "caller")
    await asyncio.wait_for(child._task, timeout=1.0)

    assert task_manager.wait_calls == 1
    assert child_outputs == ["waiting", "done"]
    assert any("background output ready" in request for request in child_requests)
    assert sent_messages == [
        {
            "from_sid": "child",
            "to_sid": "caller",
            "content": "done",
            "kind": "result",
            "error": None,
        }
    ]
