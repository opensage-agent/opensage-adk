"""Minimal example showing static subagent declaration + call_subagent / get_available_models.

(This was previously an ensemble demo. The ensemble tools have been removed —
ensemble can be expressed as multiple ``call_subagent(model_name=...)`` invocations
in driver code, but is no longer a first-class LLM tool.)
"""

from google.adk.models.lite_llm import LiteLlm

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.general.orchestration_tools import (
    call_subagent,
    get_available_models,
    list_subagents,
)


def calculate_add(a: float, b: float) -> float:
    """Calculate the sum of two numbers.

    Args:
        a: The first number to add.
        b: The second number to add.

    Returns:
        The sum of a and b.
    """
    return a + b


calculation_agent = OpenSageAgent(
    model=LiteLlm(model="openai/o4-mini"),
    name="calculation_agent",
    instruction="""
    You are a helpful math assistant. You can help users with basic arithmetic operations.
    """,
    tools=[
        calculate_add,
    ],
)


def mk_agent(opensage_session_id: str):
    root_agent = OpenSageAgent(
        model=LiteLlm(model="openai/gpt-5"),
        name="simple_math_agent",
        instruction="""
        You are a helpful math assistant. Delegate calculations to the
        ``calculation_agent`` sub-agent via the ``call_subagent`` tool.

        Use ``list_subagents`` to discover available sub-agents and
        ``get_available_models`` to see which model identifiers are valid for
        the optional ``model_name`` parameter of ``call_subagent``.

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
    return root_agent
