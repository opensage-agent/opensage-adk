from typing import Any, Dict

from google.adk.models.lite_llm import LiteLlm

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.general.dynamic_subagent import (
    call_subagent_as_tool,
    create_subagent,
    list_active_agents,
)

# Math operation tools


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
    }


def subtract_numbers(a: float, b: float) -> Dict[str, Any]:
    """
    Subtract second number from first number.

    Args:
        a: Number to subtract from
        b: Number to subtract

    Returns:
        Dictionary with subtraction result
    """
    result = a - b
    return {
        "operation": "subtraction",
        "operand_a": a,
        "operand_b": b,
        "result": result,
        "formula": f"{a} - {b} = {result}",
        "status": "completed",
    }


def multiply_numbers(a: float, b: float) -> Dict[str, Any]:
    """
    Multiply two numbers together.

    Args:
        a: First number to multiply
        b: Second number to multiply

    Returns:
        Dictionary with multiplication result
    """
    result = a * b
    return {
        "operation": "multiplication",
        "operand_a": a,
        "operand_b": b,
        "result": result,
        "formula": f"{a} × {b} = {result}",
        "status": "completed",
    }


def divide_numbers(a: float, b: float) -> Dict[str, Any]:
    """
    Divide first number by second number.

    Args:
        a: Number to divide (dividend)
        b: Number to divide by (divisor)

    Returns:
        Dictionary with division result
    """
    if b == 0:
        return {
            "operation": "division",
            "operand_a": a,
            "operand_b": b,
            "result": None,
            "formula": f"{a} ÷ {b} = Error",
            "error": "Division by zero is not allowed",
            "status": "error",
        }

    result = a / b
    return {
        "operation": "division",
        "operand_a": a,
        "operand_b": b,
        "result": result,
        "formula": f"{a} ÷ {b} = {result}",
        "status": "completed",
    }


def mk_agent(opensage_session_id: str):
    root_agent = OpenSageAgent(
        model=LiteLlm(model="openai/gpt-5"),
        name="math_root_agent",
        instruction="""
        You are a root math agent responsible for coordinating mathematical calculations through specialized sub-agents.
        You should always delegate subtasks to the specialized sub-agents.
        You should not perform the calculation yourself.
        Formulate the final answer as a single number inside <final_answer>...</final_answer> tags.
        """,
        description="Root math agent that dynamically creates and manages specialized math sub-agents for calculations.",
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
    return root_agent
