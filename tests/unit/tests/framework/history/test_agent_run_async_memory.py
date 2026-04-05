from __future__ import annotations

from types import SimpleNamespace

import pytest

from opensage.memory.file_based.short_term import session_files
from opensage.memory.file_based.short_term.session_files import (
    compute_host_root_mem_dir,
)
from opensage.patches import agent_run_async


def test_compute_agent_mem_dir_uses_nested_session_layout() -> None:
    invocation_context = SimpleNamespace(
        session=SimpleNamespace(
            id="sess-1",
            state={"_mem_agent_dir": "/mem/short_term/Agent_Alpha__sess-1"},
        ),
        agent=SimpleNamespace(name="Agent Alpha"),
    )

    agent_mem_dir = session_files._compute_agent_mem_dir(invocation_context)

    assert agent_mem_dir == "/mem/short_term/Agent_Alpha__sess-1"


def test_compute_root_session_mem_dir_uses_agent_and_session_ids() -> None:
    assert (
        session_files.compute_root_session_mem_dir(
            agent_name="Agent Alpha",
            session_id="sess-1",
        )
        == "/mem/short_term/Agent_Alpha__sess-1"
    )


def test_compute_child_session_mem_dir_nests_under_parent() -> None:
    parent_context = SimpleNamespace(
        session=SimpleNamespace(
            id="root-session",
            state={"_mem_agent_dir": "/mem/short_term/root_agent__root-session"},
        ),
        agent=SimpleNamespace(name="root_agent"),
    )

    child_mem_dir = session_files._compute_child_session_mem_dir(
        parent_context,
        child_agent_name="child agent",
        child_session_id="child-session",
    )

    assert (
        child_mem_dir
        == "/mem/short_term/root_agent__root-session/child_agent__child-session"
    )


def test_inject_runtime_memory_context_adds_file_session_specific_block() -> None:
    agent = SimpleNamespace(name="agent", instruction="Base instruction")

    original_instruction = agent_run_async._inject_runtime_memory_context(
        agent,
        memory_management="file",
        session_id="sess-42",
        agent_mem_dir="/mem/short_term/agent__sess-42",
    )

    assert original_instruction == "Base instruction"
    assert "sess-42" in agent.instruction
    assert "/mem/short_term/agent__sess-42" in agent.instruction
    assert "traj.json" in agent.instruction
    assert "TODO.md" in agent.instruction


def test_clone_agent_for_child_session_does_not_preinject_runtime_context() -> None:
    agent = SimpleNamespace(name="agent", instruction="Base instruction")

    cloned_agent = agent_run_async._clone_agent_for_child_session(agent)

    assert cloned_agent is not agent
    assert cloned_agent.instruction == "Base instruction"


def test_build_root_session_state_adds_mem_dir_for_file_memory(monkeypatch) -> None:
    monkeypatch.setattr(
        session_files,
        "_get_memory_management_from_opensage_session_id",
        lambda _session_id: "file",
    )

    state = session_files.build_root_session_state(
        opensage_session_id="opensage-1",
        session_id="sess-42",
        agent_name="Root Agent",
        base_state={"custom": "value"},
    )

    assert state["opensage_session_id"] == "opensage-1"
    assert state["custom"] == "value"
    assert state["_mem_agent_dir"] == "/mem/short_term/Root_Agent__sess-42"
    assert state["_host_mem_dir"] == compute_host_root_mem_dir(
        opensage_session_id="opensage-1",
        agent_name="Root Agent",
        session_id="sess-42",
    )


def test_build_root_session_state_omits_mem_dir_for_non_file_memory(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        session_files,
        "_get_memory_management_from_opensage_session_id",
        lambda _session_id: "database",
    )

    state = session_files.build_root_session_state(
        opensage_session_id="opensage-1",
        session_id="sess-42",
        agent_name="Root Agent",
        base_state={"_mem_agent_dir": "/mem/short_term/old__sess-0"},
    )

    assert state == {
        "opensage_session_id": "opensage-1",
        "_host_mem_dir": compute_host_root_mem_dir(
            opensage_session_id="opensage-1",
            agent_name="Root Agent",
            session_id="sess-42",
        ),
    }


async def _empty_agent_run(_agent, _invocation_context):
    if False:
        yield None


@pytest.mark.asyncio
async def test_wrapped_base_agent_run_requires_precreated_mem_dir_for_file_memory(
    monkeypatch,
) -> None:
    agent = SimpleNamespace(
        name="root_agent",
        instruction="Base instruction",
        tools=[],
    )
    invocation_context = SimpleNamespace(
        session=SimpleNamespace(id="sess-1", state={}),
        agent=agent,
    )

    monkeypatch.setattr(agent_run_async, "_orig_base_agent_run", _empty_agent_run)
    monkeypatch.setattr(
        agent_run_async, "_get_memory_management_from_context", lambda _ctx: "file"
    )
    monkeypatch.setattr(
        agent_run_async,
        "is_database_short_term_enabled_from_context",
        lambda _ctx: False,
    )

    with pytest.raises(ValueError, match="Session memory directory missing"):
        [
            event
            async for event in agent_run_async._wrapped_base_agent_run(
                agent, invocation_context
            )
        ]


@pytest.mark.asyncio
async def test_wrapped_base_agent_run_skips_file_memory_for_non_file_mode(
    monkeypatch,
) -> None:
    agent = SimpleNamespace(
        name="root_agent",
        instruction="Base instruction",
        tools=[],
    )
    invocation_context = SimpleNamespace(
        session=SimpleNamespace(id="sess-1", state={}),
        agent=agent,
    )
    calls = {"ensure": 0, "persist": 0}

    def _count_ensure(*args, **kwargs) -> None:
        calls["ensure"] += 1

    def _count_persist(*args, **kwargs) -> None:
        calls["persist"] += 1

    monkeypatch.setattr(agent_run_async, "_orig_base_agent_run", _empty_agent_run)
    monkeypatch.setattr(
        agent_run_async, "_get_memory_management_from_context", lambda _ctx: "database"
    )
    monkeypatch.setattr(
        agent_run_async,
        "is_database_short_term_enabled_from_context",
        lambda _ctx: False,
    )
    monkeypatch.setattr(agent_run_async, "_ensure_agent_mem_layout", _count_ensure)
    monkeypatch.setattr(agent_run_async, "_persist_traj_json", _count_persist)

    events = [
        event
        async for event in agent_run_async._wrapped_base_agent_run(
            agent, invocation_context
        )
    ]

    assert events == []
    assert calls == {"ensure": 0, "persist": 0}
    assert agent.instruction == "Base instruction"


@pytest.mark.asyncio
async def test_wrapped_base_agent_run_injects_database_memory_tool_temporarily(
    monkeypatch,
) -> None:
    injected_tool = SimpleNamespace(name="memory_management_agent")
    seen_tool_names = []

    async def _record_tools(agent, _invocation_context):
        seen_tool_names.extend(tool.name for tool in agent.tools)
        if False:
            yield None

    agent = SimpleNamespace(name="root_agent", instruction="Base instruction", tools=[])
    invocation_context = SimpleNamespace(
        session=SimpleNamespace(id="sess-1", state={}),
        agent=agent,
    )

    monkeypatch.setattr(agent_run_async, "_orig_base_agent_run", _record_tools)
    monkeypatch.setattr(
        agent_run_async, "_get_memory_management_from_context", lambda _ctx: "database"
    )
    monkeypatch.setattr(
        agent_run_async,
        "is_database_short_term_enabled_from_context",
        lambda _ctx: False,
    )

    def _inject_tools(_agent, _ctx):
        original_tools = list(_agent.tools)
        _agent.tools = original_tools + [injected_tool]
        return original_tools

    monkeypatch.setattr(agent_run_async, "_inject_runtime_memory_tools", _inject_tools)
    monkeypatch.setattr(
        agent_run_async,
        "_inject_runtime_memory_context",
        lambda _agent, **kwargs: "Base instruction",
    )

    events = [
        event
        async for event in agent_run_async._wrapped_base_agent_run(
            agent, invocation_context
        )
    ]

    assert events == []
    assert seen_tool_names == ["memory_management_agent"]
    assert agent.instruction == "Base instruction"
    assert agent.tools == []
