# Agent with Ensemble

**Source:** [`examples/agents_with_features/sample_agent_ensemble`](https://github.com/opensage-agent/opensage-adk/tree/main/examples/agents_with_features/sample_agent_ensemble)

Shows how to hand `agent_ensemble`, `get_available_agents_for_ensemble`, and `get_available_models` to the root agent so it can **fan the same sub-task out to several models** and compare answers.

The ensemble configuration is read from `config.toml`; see `[subagent] available_models_for_ensemble`.

## Agent Source Code

```python title="examples/agents_with_features/sample_agent_ensemble/agent.py"
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.agent_tool import AgentTool

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.general.agent_tools import (
    agent_ensemble,
    flag_unjustified_claims,
    get_available_agents_for_ensemble,
    get_available_models,
)


def calculate_add(a: float, b: float) -> float:
    """Calculate the sum of two numbers."""
    return a + b


calculation_agent = OpenSageAgent(
    model=LiteLlm(model="openai/gpt-5.4"),
    name="calculation_agent",
    instruction="You are a helpful math assistant.",
    tools=[calculate_add],
)

calculation_agent_tool = AgentTool(agent=calculation_agent)


def mk_agent(opensage_session_id: str):
    return OpenSageAgent(
        model=LiteLlm(model="openai/gpt-5"),
        name="simple_math_agent",
        instruction="""
        You are a helpful math assistant. Use the calculate_add tool for arithmetic.
        Formulate the final answer as a single number inside <final_answer>...</final_answer> tags.
        """,
        description="A simple math agent that can perform addition operations.",
        tools=[
            calculation_agent_tool,
            agent_ensemble,
            get_available_agents_for_ensemble,
            get_available_models,
        ],
    )
```

## Run It

```bash
uv run opensage web \
  --agent examples/agents_with_features/sample_agent_ensemble \
  --config examples/agents_with_features/sample_agent_ensemble/config.toml \
  --port 8000
```
