# Agent with a Sub-Agent Tool

**Source:** [`examples/agents_101/sample_agent_tool`](https://github.com/opensage-agent/opensage-adk/tree/main/examples/agents_101/sample_agent_tool)

Demonstrates how to wrap a sub-agent as an `AgentTool` so the root agent can call it like any other tool. The sub-agent has its own model, instruction, and tool list; the root agent simply invokes it by name.

## Agent Source Code

```python title="examples/agents_101/sample_agent_tool/agent.py"
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.agent_tool import AgentTool

from opensage.agents.opensage_agent import OpenSageAgent


def calculate_add(a: float, b: float) -> float:
    """Calculate the sum of two numbers."""
    return a + b


def calculate_subtract(a: float, b: float) -> float:
    """Calculate the difference of two numbers."""
    return a - b


calculation_agent = OpenSageAgent(
    model=LiteLlm(model="anthropic/claude-opus-4-7"),
    name="calculation_agent",
    instruction="You are a helpful math assistant.",
    tools=[calculate_add, calculate_subtract],
)

calculation_tool = AgentTool(agent=calculation_agent)

root_agent = OpenSageAgent(
    model=LiteLlm(model="openai/gpt-5.4"),
    name="simple_math_agent",
    instruction="Use the calculation_agent tool to perform arithmetic.",
    description="A simple math agent that delegates to a calculation sub-agent.",
    tools=[calculation_tool],
)


def mk_agent(opensage_session_id: str = None):
    return root_agent
```

## Run It

```bash
uv run opensage web \
  --agent examples/agents_101/sample_agent_tool \
  --port 8000
```

Open [http://localhost:8000](http://localhost:8000) and try asking the root agent to add or subtract two numbers. It will call the `calculation_agent` sub-agent under the hood, which has access to both `calculate_add` and `calculate_subtract` tools.
