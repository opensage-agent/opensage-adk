# Agent with History Summarization

**Source:** [`agent_library/agents_with_features/sample_summarization`](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents_with_features/sample_summarization)

Shows how to use `register_callback_to_all_agents` to attach **history-summarization and tool-response-summarization callbacks** across every agent in a tree. Long tool outputs are truncated/summarized to keep context size under control.

Two environment variables are set inside `mk_agent` to tune the caps:

- `MAX_HISTORY_SUMMARY_LENGTH = "300"`
- `MAX_TOOL_RESPONSE_LENGTH = "100"`

The tools deliberately return long strings (`"here is the sum: " * 100 + ...`) to exercise the summarizer.

## Agent Source Code

```python title="agent_library/agents_with_features/sample_summarization/agent.py"
from __future__ import annotations

import os
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
from opensage.utils.agent_utils import (
    discover_all_agents,
    register_callback_to_all_agents,
)


def add_numbers(a: float, b: float) -> str:
    """Add two numbers together."""
    return "here is the sum: " * 100 + str(a + b)


def multiply_numbers(a: float, b: float) -> str:
    """Multiply two numbers together."""
    return "here is the product: " * 100 + str(a * b)


def subtract_numbers(a: float, b: float) -> float:
    """Subtract two numbers."""
    return a - b


def calculate_area_and_perimeter(
    length: float, width: float, tool_context: ToolContext
) -> Dict[str, float]:
    """Calculate area and perimeter of a rectangle."""
    return {
        "area": length * width,
        "perimeter": 2 * (length + width),
        "length": "length: " * 100 + str(length),
        "width": "width: " * 100 + str(width),
    }


def mk_agent(opensage_session_id: str):
    os.environ["MAX_HISTORY_SUMMARY_LENGTH"] = "300"
    os.environ["MAX_TOOL_RESPONSE_LENGTH"] = "100"

    geometry_calculator = OpenSageAgent(
        name="geometry_calculator",
        description="Calculates geometric properties.",
        model=LiteLlm(model="openai/gpt-5.4"),
        instruction="You specialize in calculating geometric properties.",
        tools=[calculate_area_and_perimeter],
    )
    geometry_tool = AgentTool(agent=geometry_calculator)

    math_calculator = OpenSageAgent(
        name="math_calculator",
        description="Calculates multiplication.",
        model=LiteLlm(model="openai/gpt-5.4"),
        instruction="You specialize in calculating mathematical properties.",
        tools=[multiply_numbers],
    )

    return OpenSageAgent(
        name="calculation_orchestrator",
        description="Main agent that coordinates calculations.",
        model=LiteLlm(model="openai/gpt-5.4"),
        instruction="You help users with calculations. Put the final number in <final_answer>...</final_answer>.",
        tools=[
            geometry_tool,
            create_subagent,
            list_active_agents,
            call_subagent_as_tool,
            add_numbers,
            subtract_numbers,
        ],
        sub_agents=[math_calculator],
    )
```
!!! info
    The summarizer plugins themselves (`history_summarizer_plugin`, `tool_response_summarizer_plugin`) are enabled via `config.toml`. They plug in automatically once listed under `[plugins] enabled`.

## Run It

```bash
uv run opensage web \
  --agent agent_library/agents_with_features/sample_summarization \
  --config agent_library/agents_with_features/sample_summarization/config.toml \
  --port 8000
```
