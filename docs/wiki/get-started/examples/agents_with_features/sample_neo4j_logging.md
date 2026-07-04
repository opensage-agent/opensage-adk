# Agent with Neo4j Logging

**Source:** [`agent_library/agents_with_features/sample_neo4j_logging`](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents_with_features/sample_neo4j_logging)

Runs a multi-agent calculation orchestrator while **persisting every agent interaction in a Neo4j graph**. Useful when you want a queryable trace of a session: who called whom, with what arguments, and what was returned.

The Neo4j container is declared under `[sandbox.sandboxes.neo4j]` in `config.toml` and started automatically when the agent launches.

## Agent Source Code

```python title="agent_library/agents_with_features/sample_neo4j_logging/agent.py"
from __future__ import annotations

from typing import Dict

from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.tool_context import ToolContext

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.general.orchestration_tools import (
    call_subagent,
    create_subagent,
    list_subagents,
)


def add_numbers(a: float, b: float) -> float:
    """Add two numbers together."""
    return a + b


def multiply_numbers(a: float, b: float) -> float:
    """Multiply two numbers together."""
    return a * b


def calculate_area_and_perimeter(
    length: float, width: float, tool_context: ToolContext
) -> Dict[str, float]:
    """Calculate area and perimeter of a rectangle."""
    return {"area": length * width, "perimeter": 2 * (length + width)}


geometry_calculator = OpenSageAgent(
    name="geometry_calculator",
    description="Calculates geometric properties like area and perimeter of shapes.",
    model=LiteLlm(model="openai/gpt-5.5"),
    instruction="You specialize in calculating geometric properties.",
    tools=[calculate_area_and_perimeter],
)


def mk_agent(opensage_session_id: str):
    return OpenSageAgent(
        name="calculation_orchestrator",
        description="Main agent that coordinates calculations with Neo4j history logging.",
        model=LiteLlm(model="openai/gpt-5.5"),
        instruction="""
        You are a calculation orchestrator. Delegate geometric calculations to
        the `geometry_calculator` sub-agent via the `call_subagent` tool.
        Formulate the final answer as a single number inside
        <final_answer>...</final_answer> tags.
        """,
        subagents=[geometry_calculator],
        tools=[
            call_subagent,
            create_subagent,
            list_subagents,
            add_numbers,
            multiply_numbers,
        ],
    )
```

## Run It

```bash
uv run opensage web \
  --agent agent_library/agents_with_features/sample_neo4j_logging \
  --config agent_library/agents_with_features/sample_neo4j_logging/config.toml \
  --port 8000
```

Neo4j listens on `7474` (HTTP) and `7687` (Bolt) by default; browse to [http://localhost:7474](http://localhost:7474) (user `neo4j`) to query the session graph after a run.
