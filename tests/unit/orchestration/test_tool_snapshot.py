"""register_agent_tree builds a tool snapshot used by rebuild_agent_from_definition.

The snapshot captures every tool reachable from the agent tree at startup,
keyed by tool name. Same source (same object, or same ``__wrapped__``)
dedupes silently. True collisions (same name, different source) keep the
first observed and warn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from google.adk.tools.function_tool import FunctionTool

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.orchestration.manager import AgentManager


@dataclass
class _StubOpenSageSession:
    opensage_session_id: str
    session_service: object
    artifact_service: object = None
    memory_service: object = None
    credential_service: object = None


def _make_manager():
    svc = AsyncMock()
    op = _StubOpenSageSession(opensage_session_id="osid", session_service=svc)
    return AgentManager(op)  # type: ignore[arg-type]


def _mk_named_tool(name: str) -> FunctionTool:
    """Build a FunctionTool whose .name is `name`. Identity is preserved
    through OpenSageAgent's tool wrapping (BaseTool path mutates in place)."""

    def f():
        pass

    f.__name__ = name
    return FunctionTool(f)


def test_snapshot_starts_empty():
    mgr = _make_manager()
    assert mgr._tool_snapshot == {}


def test_snapshot_collects_tools_from_root():
    tool_a = _mk_named_tool("tool_a")
    tool_b = _mk_named_tool("tool_b")
    root = OpenSageAgent(
        name="root",
        model="openai/gpt-4o-mini",
        tools=[tool_a, tool_b],
    )

    mgr = _make_manager()
    mgr.register_agent_tree(root)

    assert mgr._tool_snapshot["tool_a"] is tool_a
    assert mgr._tool_snapshot["tool_b"] is tool_b


def test_snapshot_collects_tools_from_descendants():
    parent_tool = _mk_named_tool("parent_tool")
    child_tool = _mk_named_tool("child_tool")
    grandchild_tool = _mk_named_tool("grandchild_tool")

    grandchild = OpenSageAgent(
        name="grandchild",
        model="openai/gpt-4o-mini",
        tools=[grandchild_tool],
    )
    child = OpenSageAgent(
        name="child",
        model="openai/gpt-4o-mini",
        tools=[child_tool],
        subagents=[grandchild],
    )
    root = OpenSageAgent(
        name="root",
        model="openai/gpt-4o-mini",
        tools=[parent_tool],
        subagents=[child],
    )

    mgr = _make_manager()
    mgr.register_agent_tree(root)

    assert mgr._tool_snapshot["parent_tool"] is parent_tool
    assert mgr._tool_snapshot["child_tool"] is child_tool
    assert mgr._tool_snapshot["grandchild_tool"] is grandchild_tool


def test_snapshot_dedupes_same_object_same_name(caplog):
    """A BaseTool referenced by multiple agents (same object) is recorded
    once with no collision warning."""
    shared = _mk_named_tool("shared_tool")

    child = OpenSageAgent(
        name="child",
        model="openai/gpt-4o-mini",
        tools=[shared],
    )
    root = OpenSageAgent(
        name="root",
        model="openai/gpt-4o-mini",
        tools=[shared],
        subagents=[child],
    )

    mgr = _make_manager()
    with caplog.at_level(logging.WARNING):
        mgr.register_agent_tree(root)

    assert mgr._tool_snapshot["shared_tool"] is shared
    assert not any("appears in multiple agents" in r.message for r in caplog.records)


def test_snapshot_dedupes_wrapped_function_via_wrapped_attr(caplog):
    """Plain functions get re-wrapped by each OpenSageAgent.__init__ — but
    both wrappers point to the same source via ``__wrapped__``, so the
    snapshot recognizes them as the same source (no false collision)."""

    def shared_func():
        pass

    shared_func.__name__ = "shared_func"

    child = OpenSageAgent(
        name="child",
        model="openai/gpt-4o-mini",
        tools=[shared_func],
    )
    root = OpenSageAgent(
        name="root",
        model="openai/gpt-4o-mini",
        tools=[shared_func],
        subagents=[child],
    )

    mgr = _make_manager()
    with caplog.at_level(logging.WARNING):
        mgr.register_agent_tree(root)

    # Snapshot has it; no collision warning emitted.
    assert "shared_func" in mgr._tool_snapshot
    assert not any("appears in multiple agents" in r.message for r in caplog.records)


def test_snapshot_keeps_first_warns_on_real_collision(caplog):
    """Same name, different source objects: keep first observed, log warning."""
    first = _mk_named_tool("conflict")
    second = _mk_named_tool("conflict")
    assert first is not second

    # Walk order is depth-first: root tools collected before descending into
    # subagents. So root's `first` is observed before child's `second`.
    child = OpenSageAgent(
        name="child",
        model="openai/gpt-4o-mini",
        tools=[second],
    )
    root = OpenSageAgent(
        name="root",
        model="openai/gpt-4o-mini",
        tools=[first],
        subagents=[child],
    )

    mgr = _make_manager()
    with caplog.at_level(logging.WARNING):
        mgr.register_agent_tree(root)

    assert mgr._tool_snapshot["conflict"] is first
    assert any(
        "appears in multiple agents" in r.message and "conflict" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_rebuild_uses_snapshot():
    """rebuild_agent_from_definition resolves non-baseline names from the
    manager's startup tool snapshot."""
    from opensage.toolbox.general.orchestration_tools import (
        rebuild_agent_from_definition,
    )

    custom_tool = _mk_named_tool("custom_tool")
    root = OpenSageAgent(
        name="root",
        model="openai/gpt-4o-mini",
        tools=[custom_tool],
    )

    mgr = _make_manager()
    mgr.register_agent_tree(root)

    class _Llms:
        def __contains__(self, name):
            return name == "openai/gpt-4o-mini"

        def get(self, name):
            return root.model

        def list_names(self):
            return ["openai/gpt-4o-mini"]

    mgr._opensage_session.llms = _Llms()  # type: ignore[attr-defined]
    mgr._opensage_session.agent_manager = mgr  # type: ignore[attr-defined]

    rebuilt = await rebuild_agent_from_definition(
        {
            "name": "dynamic_worker",
            "instruction": "do work",
            "model": "openai/gpt-4o-mini",
            "tool_names": ["custom_tool"],
        },
        mgr._opensage_session,  # type: ignore[arg-type]
    )

    # The rebuilt agent should contain custom_tool (or a wrap of it).
    rebuilt_names = {
        getattr(t, "name", getattr(t, "__name__", "")) for t in rebuilt.tools
    }
    assert "custom_tool" in rebuilt_names


@pytest.mark.asyncio
async def test_rebuild_drops_unknown_tool_names(caplog):
    """Names not in baseline and not in the snapshot are dropped with a
    single warning."""
    from opensage.toolbox.general.orchestration_tools import (
        rebuild_agent_from_definition,
    )

    root = OpenSageAgent(name="root", model="openai/gpt-4o-mini", tools=[])

    mgr = _make_manager()
    mgr.register_agent_tree(root)

    class _Llms:
        def __contains__(self, name):
            return name == "openai/gpt-4o-mini"

        def get(self, name):
            return root.model

        def list_names(self):
            return ["openai/gpt-4o-mini"]

    mgr._opensage_session.llms = _Llms()  # type: ignore[attr-defined]
    mgr._opensage_session.agent_manager = mgr  # type: ignore[attr-defined]

    with caplog.at_level(logging.WARNING):
        rebuilt = await rebuild_agent_from_definition(
            {
                "name": "dynamic_worker",
                "instruction": "do work",
                "model": "openai/gpt-4o-mini",
                "tool_names": ["nonexistent_tool"],
            },
            mgr._opensage_session,  # type: ignore[arg-type]
        )

    rebuilt_names = {
        getattr(t, "name", getattr(t, "__name__", "")) for t in rebuilt.tools
    }
    assert "nonexistent_tool" not in rebuilt_names
    assert any("nonexistent_tool" in r.message for r in caplog.records)
