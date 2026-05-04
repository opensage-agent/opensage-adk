# Agent with Tool Combo

**Source:** [`examples/agents_with_features/sample_tool_combo`](https://github.com/opensage-agent/opensage-adk/tree/main/examples/agents_with_features/sample_tool_combo)

Demonstrates the `ToolCombo` feature, which **chains several tools into one atomic "tool" call**. The `return_history` flag controls whether the caller LLM sees the intermediate steps.

## Agent Source Code

```python title="examples/agents_with_features/sample_tool_combo/agent.py"
from typing import Any, Dict

from google.adk.models.lite_llm import LiteLlm

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.features.tool_combo import ToolCombo


def add_numbers(a: float, b: float) -> Dict[str, Any]:
    """Add two numbers together."""
    return {"result": a + b, "formula": f"{a} + {b} = {a + b}"}


def multiply_by_two(result: float) -> Dict[str, Any]:
    """Multiply a number by 2."""
    return {"result": result * 2, "formula": f"{result} × 2 = {result * 2}"}


def mk_agent(opensage_session_id: str):
    # two-step operation, shows intermediate steps
    simple_combo_with_history = ToolCombo(
        name="simple_combo_with_history",
        tool_sequences=[add_numbers, multiply_by_two],
        description="Add two numbers and multiply by 2. Shows intermediate steps.",
        model=LiteLlm(model="openai/gpt-5.4"),
        return_history=True,
    )

    # two-step operation, only shows final result
    simple_combo_without_history = ToolCombo(
        name="simple_combo_without_history",
        tool_sequences=[add_numbers, multiply_by_two],
        description="Add two numbers and multiply by 2. Only shows final result.",
        model=LiteLlm(model="openai/gpt-5.4"),
        return_history=False,
    )

    return OpenSageAgent(
        name="tool_combo_demo_agent",
        model=LiteLlm(model="openai/gpt-5.4"),
        description="Demonstrates ToolCombo functionality with return_history True and False.",
        instruction="""
        You are a calculator agent that demonstrates different ToolCombo configurations.
        Formulate the final answer as a single number inside <final_answer>...</final_answer> tags.
        """,
        tools=[],
        tool_combos=[
            simple_combo_with_history,
            simple_combo_without_history,
        ],
    )
```

## Run It

```bash
uv run opensage web \
  --agent examples/agents_with_features/sample_tool_combo \
  --config examples/agents_with_features/sample_tool_combo/config.toml \
  --port 8000
```
