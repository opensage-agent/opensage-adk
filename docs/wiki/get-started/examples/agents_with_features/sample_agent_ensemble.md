# Agent with Multi-Model Delegation

**Source:** [`agent_library/agents_with_features/sample_agent_ensemble`](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents_with_features/sample_agent_ensemble)

Shows how to declare a static sub-agent and hand the root agent `call_subagent`, `list_subagents`, and `get_available_models` so it can **delegate a sub-task to a specific model**.

!!! note "Ensemble tools were removed"
    This example was previously an *ensemble* demo. The dedicated ensemble tools
    (`agent_ensemble`, `get_available_agents_for_ensemble`) have been removed. An
    ensemble can still be expressed as multiple `call_subagent(model_name=...)`
    invocations in driver code, but it is no longer a first-class LLM tool.
    Use `get_available_models` to discover valid `model_name` values.

## Agent Source Code

```python title="agent_library/agents_with_features/sample_agent_ensemble/agent.py"
from google.adk.models.lite_llm import LiteLlm

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.general.orchestration_tools import (
    call_subagent,
    get_available_models,
    list_subagents,
)


def calculate_add(a: float, b: float) -> float:
    """Calculate the sum of two numbers."""
    return a + b


calculation_agent = OpenSageAgent(
    model=LiteLlm(model="openai/gpt-5.5"),
    name="calculation_agent",
    instruction="You are a helpful math assistant.",
    tools=[calculate_add],
)


def mk_agent(opensage_session_id: str):
    return OpenSageAgent(
        model=LiteLlm(model="openai/gpt-5"),
        name="simple_math_agent",
        instruction="""
        You are a helpful math assistant. Delegate calculations to the
        `calculation_agent` sub-agent via the `call_subagent` tool.

        Use `list_subagents` to discover available sub-agents and
        `get_available_models` to see which model identifiers are valid for the
        optional `model_name` parameter of `call_subagent`.

        Formulate the final answer as a single number inside
        <final_answer>...</final_answer> tags.
        """,
        description="A simple math agent that delegates addition to a calculation sub-agent.",
        subagents=[calculation_agent],
        tools=[
            call_subagent,
            list_subagents,
            get_available_models,
        ],
    )
```

## Run It

```bash
uv run opensage web \
  --agent agent_library/agents_with_features/sample_agent_ensemble \
  --config agent_library/agents_with_features/sample_agent_ensemble/config.toml \
  --port 8000
```
