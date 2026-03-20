"""
Sample ToolCombo Agent - Four Operations Calculator

This example demonstrates how to use ToolCombo with return_history True and False.
It shows how to create tool sequences for arithmetic operations and demonstrates
the different behaviors of return_history parameter.

Key Features Demonstrated:
1. ToolCombo with return_history=True (shows intermediate results)
2. ToolCombo with return_history=False (hides intermediate results)
3. Sequential tool execution with arithmetic operations
4. Comparing different ToolCombo configurations
"""

import os
from typing import Any, Dict

from google.adk.models.lite_llm import LiteLlm

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.features.tool_combo import ToolCombo


# Arithmetic operation tools
def add_numbers(a: float, b: float) -> Dict[str, Any]:
    """
    Add two numbers together.

    Args:
        a: First number to add
        b: Second number to add

    Returns:
        Dictionary with addition result
    """
    result = a + b
    return {
        "operation": "addition",
        "operand_a": a,
        "operand_b": b,
        "result": result,
        "formula": f"{a} + {b} = {result}",
        "step": "Step 1: Addition completed",
    }


def multiply_by_two(result: float) -> Dict[str, Any]:
    """
    Multiply a number by 2.

    Args:
        result: Number to multiply by 2

    Returns:
        Dictionary with multiplication result
    """
    multiplied = result * 2
    return {
        "operation": "multiply_by_two",
        "input": result,
        "result": multiplied,
        "formula": f"{result} × 2 = {multiplied}",
        "step": "Step 2: Multiplication by 2 completed",
    }


def mk_agent(opensage_session_id: str):
    # two-step operation, shows intermediate steps
    simple_combo_with_history = ToolCombo(
        name="simple_combo_with_history",
        tool_sequences=[add_numbers, multiply_by_two],
        description="Simple two-step calculation: Add two numbers and multiply by 2. Shows intermediate steps.",
        model=LiteLlm(model="openai/o4-mini"),
        return_history=True,
    )

    # two-step operation, only shows final result
    simple_combo_without_history = ToolCombo(
        name="simple_combo_without_history",
        tool_sequences=[add_numbers, multiply_by_two],
        description="Simple two-step calculation: Add two numbers and multiply by 2. Only shows final result.",
        model=LiteLlm(model="openai/o4-mini"),
        return_history=False,
    )
    root_agent = OpenSageAgent(
        name="tool_combo_demo_agent",
        model=LiteLlm(model="openai/o4-mini"),
        description="Demonstrates ToolCombo functionality with return_history True and False settings.",
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
    return root_agent
