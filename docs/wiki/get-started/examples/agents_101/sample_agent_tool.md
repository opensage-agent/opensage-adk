# Agent with a Sub-Agent

**Source:** [`agent_library/agents_101/sample_agent_tool`](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents_101/sample_agent_tool)

Demonstrates how to declare a sub-agent via `subagents=[...]` and let the root agent invoke it with the `call_subagent` tool. The sub-agent has its own model, instruction, and tool list; the root agent delegates to it by name.

!!! note "OpenSageAgent forbids `AgentTool` and ADK `sub_agents=`"
    Sub-agents in OpenSage are declared through the `subagents=[...]` field and
    invoked with `call_subagent` / `list_subagents` from
    `opensage.toolbox.general.orchestration_tools`. Passing an `AgentTool` in
    `tools=` or using ADK's `sub_agents=` raises a `ValueError`.

## Agent Source Code

```python title="agent_library/agents_101/sample_agent_tool/agent.py"
from google.adk.models.lite_llm import LiteLlm

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.general.orchestration_tools import (
    call_subagent,
    list_subagents,
)


def calculate_add(a: float, b: float) -> float:
    """Calculate the sum of two numbers."""
    return a + b


def calculate_subtract(a: float, b: float) -> float:
    """Calculate the difference of two numbers."""
    return a - b


calculation_agent = OpenSageAgent(
    model=LiteLlm(model="anthropic/claude-opus-4-8"),
    name="calculation_agent",
    description="A math sub-agent that performs basic arithmetic operations.",
    instruction="You are a helpful math assistant.",
    tools=[calculate_add],
)

root_agent = OpenSageAgent(
    model=LiteLlm(model="openai/gpt-5.5"),
    name="simple_math_agent",
    instruction="""
    When a user asks you to add two numbers, delegate the calculation to the
    `calculation_agent` sub-agent via the `call_subagent` tool.
    """,
    description="A simple math agent that delegates to a calculation sub-agent.",
    subagents=[calculation_agent],
    tools=[
        call_subagent,
        list_subagents,
    ],
)


def mk_agent(opensage_session_id: str = None):
    return root_agent
```

## Run It

```bash
uv run opensage web \
  --agent agent_library/agents_101/sample_agent_tool \
  --port 8000
```

Open [http://localhost:8000](http://localhost:8000) and try asking the root agent to add two numbers. It will delegate to the `calculation_agent` sub-agent under the hood via `call_subagent`.
