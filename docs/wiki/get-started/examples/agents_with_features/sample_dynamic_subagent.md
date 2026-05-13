# Agent with Dynamic Sub-Agents

**Source:** [`agent_library/agents_with_features/sample_dynamic_subagent`](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents_with_features/sample_dynamic_subagent)

Shows the **dynamic sub-agent** pattern: the root agent is given `create_subagent`, `list_active_agents`, and `call_subagent_as_tool` so it can spin up specialized sub-agents at runtime and delegate to them.

## Agent Source Code

```python title="agent_library/agents_with_features/sample_dynamic_subagent/agent.py"
from typing import Any, Dict

from google.adk.models.lite_llm import LiteLlm

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.general.dynamic_subagent import (
    call_subagent_as_tool,
    create_subagent,
    list_active_agents,
)


def add_numbers(a: float, b: float) -> Dict[str, Any]:
    """Add two numbers together."""
    return {"result": a + b, "formula": f"{a} + {b} = {a + b}"}


def subtract_numbers(a: float, b: float) -> Dict[str, Any]:
    """Subtract second number from first number."""
    return {"result": a - b, "formula": f"{a} - {b} = {a - b}"}


def multiply_numbers(a: float, b: float) -> Dict[str, Any]:
    """Multiply two numbers together."""
    return {"result": a * b, "formula": f"{a} × {b} = {a * b}"}


def divide_numbers(a: float, b: float) -> Dict[str, Any]:
    """Divide first number by second number."""
    if b == 0:
        return {"error": "Division by zero is not allowed"}
    return {"result": a / b, "formula": f"{a} ÷ {b} = {a / b}"}


def mk_agent(opensage_session_id: str):
    return OpenSageAgent(
        model=LiteLlm(model="openai/gpt-5"),
        name="math_root_agent",
        instruction="""
        You are a root math agent responsible for coordinating mathematical
        calculations through specialized sub-agents. You should always delegate
        subtasks to the specialized sub-agents — do not perform the calculation
        yourself. Formulate the final answer as a single number inside
        <final_answer>...</final_answer> tags.
        """,
        description="Root math agent that dynamically creates and manages specialized math sub-agents.",
        tools=[
            create_subagent,
            list_active_agents,
            call_subagent_as_tool,
            add_numbers,
            subtract_numbers,
            multiply_numbers,
            divide_numbers,
        ],
    )
```

> The body of each numeric tool has been abbreviated above; see the [repo](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents_with_features/sample_dynamic_subagent) for the full implementation.

## Run It

```bash
uv run opensage web \
  --agent agent_library/agents_with_features/sample_dynamic_subagent \
  --config agent_library/agents_with_features/sample_dynamic_subagent/config.toml \
  --port 8000
```

If you want dynamic sub-agents to persist across runs, set `agent_storage_path` in `config.toml`.
