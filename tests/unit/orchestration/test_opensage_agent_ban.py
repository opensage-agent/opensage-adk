"""Test that OpenSageAgent rejects AgentTool in tools=[...]."""

from __future__ import annotations

import pytest

from opensage.agents.opensage_agent import OpenSageAgent


def test_opensage_agent_rejects_agent_tool():
    from google.adk.agents.llm_agent import LlmAgent
    from google.adk.tools.agent_tool import AgentTool

    inner = LlmAgent(name="inner", model="openai/gpt-4o-mini")

    with pytest.raises(ValueError, match="AgentTool is forbidden"):
        OpenSageAgent(
            name="bad",
            model="openai/gpt-4o-mini",
            tools=[AgentTool(agent=inner)],
        )


def test_opensage_agent_subagents_validated():
    """subagents must contain OpenSageAgent instances only."""
    from google.adk.agents.llm_agent import LlmAgent

    inner_llm = LlmAgent(name="llm_inner", model="openai/gpt-4o-mini")

    with pytest.raises(ValueError, match="subagents must contain OpenSageAgent"):
        OpenSageAgent(
            name="bad",
            model="openai/gpt-4o-mini",
            tools=[],
            subagents=[inner_llm],
        )


def test_opensage_agent_accepts_subagents():
    child = OpenSageAgent(
        name="child",
        model="openai/gpt-4o-mini",
        tools=[],
    )
    parent = OpenSageAgent(
        name="parent",
        model="openai/gpt-4o-mini",
        tools=[],
        subagents=[child],
    )
    assert parent._subagents == [child]
