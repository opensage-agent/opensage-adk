# Agent with Neo4j Logging

**Source:** [`examples/agents_with_features/sample_neo4j_logging`](https://github.com/opensage-agent/opensage-adk/tree/main/examples/agents_with_features/sample_neo4j_logging)

Runs a multi-agent calculation orchestrator while **persisting every agent interaction in a Neo4j graph**. Useful when you want a queryable trace of a session: who called whom, with what arguments, and what was returned.

The Neo4j container is declared under `[sandbox.sandboxes.neo4j]` in `config.toml` and started automatically when the agent launches.

## Agent Source Code

```python title="examples/agents_with_features/sample_neo4j_logging/agent.py"
from __future__ import annotations

from typing import Dict

from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.tool_context import ToolContext

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.general.dynamic_subagent import (
    call_subagent_as_tool,
    create_subagent,
    list_active_agents,
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
    model=LiteLlm(model="openai/gpt-5.4"),
    instruction="You specialize in calculating geometric properties.",
    tools=[calculate_area_and_perimeter],
)

geometry_tool = AgentTool(agent=geometry_calculator)


def mk_agent(opensage_session_id: str):
    return OpenSageAgent(
        name="calculation_orchestrator",
        description="Main agent that coordinates calculations with Neo4j history logging.",
        model=LiteLlm(model="openai/gpt-5.4"),
        instruction="""
        You are a calculation orchestrator. Formulate the final answer as a
        single number inside <final_answer>...</final_answer> tags.
        """,
        tools=[
            geometry_tool,
            call_subagent_as_tool,
            create_subagent,
            list_active_agents,
            add_numbers,
            multiply_numbers,
        ],
    )
```

## Run It

```bash
uv run opensage web \
  --agent examples/agents_with_features/sample_neo4j_logging \
  --config examples/agents_with_features/sample_neo4j_logging/config.toml \
  --port 8000
```

Neo4j listens on `7474` (HTTP) and `7687` (Bolt) by default; browse to [http://localhost:7474](http://localhost:7474) (user `neo4j`) to query the session graph after a run.
