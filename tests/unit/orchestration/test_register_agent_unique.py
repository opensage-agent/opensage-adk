"""AgentManager.register_agent must enforce unique names (no overwrite)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

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


def test_register_agent_first_time_ok():
    mgr = _make_manager()
    # Use a plain object as a stand-in; register_agent doesn't inspect it.
    mgr.register_agent("coder", object())
    assert mgr.get_agent("coder") is not None


def test_register_agent_duplicate_raises():
    mgr = _make_manager()
    mgr.register_agent("coder", object())
    with pytest.raises(ValueError, match="already registered"):
        mgr.register_agent("coder", object())


def test_register_agent_no_overwrite_kwarg():
    """The overwrite parameter is intentionally removed."""
    import inspect

    sig = inspect.signature(AgentManager.register_agent)
    assert "overwrite" not in sig.parameters


def test_register_agent_tree_registers_root_and_descendants():
    """register_agent_tree walks root._subagents recursively."""
    from opensage.agents.opensage_agent import OpenSageAgent

    grandchild = OpenSageAgent(name="grandchild", model="openai/gpt-4o-mini", tools=[])
    child = OpenSageAgent(
        name="child",
        model="openai/gpt-4o-mini",
        tools=[],
        subagents=[grandchild],
    )
    root = OpenSageAgent(
        name="root",
        model="openai/gpt-4o-mini",
        tools=[],
        subagents=[child],
    )

    mgr = _make_manager()
    mgr.register_agent_tree(root)

    assert mgr.get_agent("root") is root
    assert mgr.get_agent("child") is child
    assert mgr.get_agent("grandchild") is grandchild


def test_register_agent_tree_duplicate_in_tree_raises():
    """Duplicate names inside the tree fail fast."""
    from opensage.agents.opensage_agent import OpenSageAgent

    a = OpenSageAgent(name="dup", model="openai/gpt-4o-mini", tools=[])
    b = OpenSageAgent(name="dup", model="openai/gpt-4o-mini", tools=[])
    root = OpenSageAgent(
        name="root",
        model="openai/gpt-4o-mini",
        tools=[],
        subagents=[a, b],
    )

    mgr = _make_manager()
    with pytest.raises(ValueError, match="already registered"):
        mgr.register_agent_tree(root)


def test_register_agent_tree_handles_shared_agent():
    """Same agent object reachable twice through distinct paths shouldn't
    double-register (we track by object id)."""
    from opensage.agents.opensage_agent import OpenSageAgent

    shared = OpenSageAgent(name="shared", model="openai/gpt-4o-mini", tools=[])
    parent_a = OpenSageAgent(
        name="a",
        model="openai/gpt-4o-mini",
        tools=[],
        subagents=[shared],
    )
    parent_b = OpenSageAgent(
        name="b",
        model="openai/gpt-4o-mini",
        tools=[],
        subagents=[shared],
    )
    root = OpenSageAgent(
        name="root",
        model="openai/gpt-4o-mini",
        tools=[],
        subagents=[parent_a, parent_b],
    )

    mgr = _make_manager()
    mgr.register_agent_tree(root)

    # 4 unique agents: root, a, b, shared
    assert set(mgr.list_agents().keys()) == {"root", "a", "b", "shared"}
