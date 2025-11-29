"""
Sample RewardLogger Agent - Mathematical Operations with Reward Tracking

This example demonstrates how to use RewardLogger with both tool_name and agent_name configurations.
It shows how to track and reward different behaviors in mathematical operations.

Key Features Demonstrated:
1. RewardLogger with tool_name configuration (tracking specific tool usage)
2. RewardLogger with agent_name configuration (tracking agent responses)
3. Different reward functions for different behaviors
4. Reward logging to files for analysis
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.function_tool import FunctionTool

from aigise.agents.aigise_agent import AigiseAgent
from aigise.features.reward_logger import RewardLogger


# Mathematical operation tools
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
        "status": "completed",
        "is_positive": result > 0,
        "is_large": result > 100,
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
        "is_positive": result > 0,
        "is_large": result > 100,
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
            "is_positive": False,
            "is_large": False,
        }

    result = a / b
    return {
        "operation": "division",
        "operand_a": a,
        "operand_b": b,
        "result": result,
        "formula": f"{a} ÷ {b} = {result}",
        "status": "completed",
        "is_positive": result > 0,
        "is_large": result > 100,
    }


def power_operation(a: float, b: float) -> Dict[str, Any]:
    """
    Raise first number to the power of second number.

    Args:
        a: Base number
        b: Exponent

    Returns:
        Dictionary with power operation result
    """
    try:
        result = a**b
        return {
            "operation": "power",
            "operand_a": a,
            "operand_b": b,
            "result": result,
            "formula": f"{a}^{b} = {result}",
            "status": "completed",
            "is_positive": result > 0,
            "is_large": result > 100,
        }
    except Exception as e:
        return {
            "operation": "power",
            "operand_a": a,
            "operand_b": b,
            "result": None,
            "formula": f"{a}^{b} = Error",
            "error": str(e),
            "status": "error",
            "is_positive": False,
            "is_large": False,
        }


# Reward Functions for Tool-based Logging


def positive_result_reward(
    tool_response: Dict[str, Any], message: Optional[str]
) -> float:
    """
    Reward function for tools that produce positive results.
    Returns 1.0 for positive results, 0.0 for negative or error results.
    """
    if tool_response.get("is_positive", False):
        return 1.0
    return 0.0


def large_result_reward(tool_response: Dict[str, Any], message: Optional[str]) -> float:
    """
    Reward function for tools that produce large results (> 100).
    Returns 1.0 for large results, 0.5 for small positive results, 0.0 for negative/error.
    """
    if tool_response.get("status") == "error":
        return 0.0
    elif tool_response.get("is_large", False):
        return 1.0
    elif tool_response.get("is_positive", False):
        return 0.5
    else:
        return 0.0


def error_handling_reward(
    tool_response: Dict[str, Any], message: Optional[str]
) -> float:
    """
    Reward function that gives negative reward for errors.
    Returns -1.0 for errors, 1.0 for successful operations.
    """
    if tool_response.get("status") == "error":
        return -1.0
    elif tool_response.get("status") == "completed":
        return 1.0
    else:
        return 0.0


# Reward Functions for Agent-based Logging


def explanation_quality_reward(
    agent_result: Dict[str, Any], message: Optional[str]
) -> float:
    """
    Reward function for agent responses that provide good explanations.
    Returns higher rewards for responses that explain the mathematical process.
    """
    response_text = agent_result.get("response", "")
    if not response_text:
        return 0.0

    response_str = str(response_text).lower()

    # Check for explanation keywords
    explanation_keywords = [
        "formula",
        "calculation",
        "step",
        "result",
        "because",
        "since",
        "therefore",
    ]
    explanation_count = sum(
        1 for keyword in explanation_keywords if keyword in response_str
    )

    # Reward based on number of explanation elements
    if explanation_count >= 4:
        return 1.0
    elif explanation_count >= 2:
        return 0.7
    elif explanation_count >= 1:
        return 0.4
    else:
        return 0.1


def mathematical_accuracy_reward(
    agent_result: Dict[str, Any], message: Optional[str]
) -> float:
    """
    Reward function for agent responses that demonstrate mathematical accuracy.
    Returns higher rewards for responses that include correct mathematical terminology.
    """
    response_text = agent_result.get("response", "")
    if not response_text:
        return 0.0

    response_str = str(response_text).lower()

    # Check for mathematical terminology
    math_terms = [
        "sum",
        "product",
        "quotient",
        "power",
        "exponent",
        "dividend",
        "divisor",
        "operand",
    ]
    math_count = sum(1 for term in math_terms if term in response_str)

    # Check for correct mathematical symbols or operations
    math_symbols = ["+", "-", "×", "÷", "=", "^", "*", "/"]
    symbol_count = sum(1 for symbol in math_symbols if symbol in response_str)

    total_score = (math_count * 0.2) + (symbol_count * 0.1)
    return min(1.0, total_score)  # Cap at 1.0


# Create RewardLoggers for tool-based tracking

# Track positive results for addition operations
addition_positive_logger = RewardLogger(
    reward_function=positive_result_reward,
    tool_name="add_numbers",
    log_dir=str(Path(__file__).parent / ".logs/tool_rewards"),
)

# Track large results for multiplication operations
multiplication_large_logger = RewardLogger(
    reward_function=large_result_reward,
    tool_name="multiply_numbers",
    log_dir=str(Path(__file__).parent / ".logs/tool_rewards"),
)

# Track error handling for division operations
division_error_logger = RewardLogger(
    reward_function=error_handling_reward,
    tool_name="divide_numbers",
    log_dir=str(Path(__file__).parent / ".logs/tool_rewards"),
)

# Track large results for power operations
power_large_logger = RewardLogger(
    reward_function=large_result_reward,
    tool_name="power_operation",
    log_dir=str(Path(__file__).parent / ".logs/tool_rewards"),
)

# Create RewardLoggers for agent-based tracking

# Track explanation quality for the main agent
explanation_logger = RewardLogger(
    reward_function=explanation_quality_reward,
    agent_name="math_reward_demo_agent",
    log_dir=str(Path(__file__).parent / ".logs/agent_rewards"),
)

# Track mathematical accuracy for the main agent
accuracy_logger = RewardLogger(
    reward_function=mathematical_accuracy_reward,
    agent_name="math_reward_demo_agent",
    log_dir=str(Path(__file__).parent / ".logs/agent_rewards"),
)


def mk_agent():
    root_agent = AigiseAgent(
        name="math_reward_demo_agent",
        model=LiteLlm(model="openai/gpt-5"),
        description="Demonstrates RewardLogger functionality with tool_name and agent_name configurations.",
        instruction="""
        You are a mathematical operations agent that demonstrates reward logging functionality.
        Formulate the final answer as a single number inside <final_answer>...</final_answer> tags.
        """,
        tools=[add_numbers, multiply_numbers, divide_numbers, power_operation],
        reward_loggers=[
            # Tool-specific reward loggers
            addition_positive_logger,
            multiplication_large_logger,
            division_error_logger,
            power_large_logger,
            # Agent-specific reward loggers
            explanation_logger,
            accuracy_logger,
        ],
    )
    return root_agent


root_agent = mk_agent()
