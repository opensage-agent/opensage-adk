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
from typing import Dict, Any
from google.adk.tools.function_tool import FunctionTool
from google.adk.models.lite_llm import LiteLlm
from aigise.extended_features.sec_agent import SecAgent
from aigise.extended_features.tool_combo_manager import ToolCombo
from dotenv import load_dotenv

# Disable OpenTelemetry to avoid context management issues
os.environ["OTEL_SDK_DISABLED"] = "true"
load_dotenv()

MODEL_NAME = os.getenv("MODEL_NAME", "anthropic/claude-sonnet-4-20250514")


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
        "step": "Step 1: Addition completed"
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
        "step": "Step 2: Multiplication by 2 completed"
    }


def subtract_ten(result: float) -> Dict[str, Any]:
    """
    Subtract 10 from a number.
    
    Args:
        result: Number to subtract 10 from
        
    Returns:
        Dictionary with subtraction result
    """
    subtracted = result - 10
    return {
        "operation": "subtract_ten",
        "input": result,
        "result": subtracted,
        "formula": f"{result} - 10 = {subtracted}",
        "step": "Step 3: Subtraction of 10 completed"
    }


def square_number(result: float) -> Dict[str, Any]:
    """
    Square a number.
    
    Args:
        result: Number to square
        
    Returns:
        Dictionary with square result
    """
    squared = result ** 2
    return {
        "operation": "square",
        "input": result,
        "result": squared,
        "formula": f"{result}² = {squared}",
        "step": "Step 4: Squaring completed"
    }


# Create ToolCombo with return_history=True (shows all intermediate steps)
calculation_combo_with_history = ToolCombo(
    name="calculation_combo_with_history",
    tool_sequences=[add_numbers, multiply_by_two, subtract_ten, square_number],
    description="Multi-step calculation: Add two numbers, multiply by 2, subtract 10, then square the result. Shows all intermediate steps.",
    model=LiteLlm(model=MODEL_NAME),
    return_history=True,  # This will create sub-agents and show intermediate results
)

# Create ToolCombo with return_history=False (only shows final result)
calculation_combo_without_history = ToolCombo(
    name="calculation_combo_without_history",
    tool_sequences=[add_numbers, multiply_by_two, subtract_ten, square_number],
    description="Multi-step calculation: Add two numbers, multiply by 2, subtract 10, then square the result. Only shows final result.",
    model=LiteLlm(model=MODEL_NAME),
    return_history=False,  # This will create a single tool that returns only the final result
)

# Create another combo for comparison - simple two-step operation
simple_combo_with_history = ToolCombo(
    name="simple_combo_with_history",
    tool_sequences=[add_numbers, multiply_by_two],
    description="Simple two-step calculation: Add two numbers and multiply by 2. Shows intermediate steps.",
    model=LiteLlm(model=MODEL_NAME),
    return_history=True,
)

simple_combo_without_history = ToolCombo(
    name="simple_combo_without_history", 
    tool_sequences=[add_numbers, multiply_by_two],
    description="Simple two-step calculation: Add two numbers and multiply by 2. Only shows final result.",
    model=LiteLlm(model=MODEL_NAME),
    return_history=False,
)


root_agent = SecAgent(
    name="tool_combo_demo_agent",
    model=LiteLlm(model=MODEL_NAME),
    description="Demonstrates ToolCombo functionality with return_history True and False settings.",
    instruction="""
    You are a calculator agent that demonstrates different ToolCombo configurations.
    
    You have access to several tool combinations and individual tools:
    
    1. TOOL COMBOS WITH RETURN_HISTORY=TRUE:
       - calculation_combo_with_history: 4-step calculation (add, multiply by 2, subtract 10, square)
       - simple_combo_with_history: 2-step calculation (add, multiply by 2)
       
    2. TOOL COMBOS WITH RETURN_HISTORY=FALSE:
       - calculation_combo_without_history: Same 4-step calculation but only shows final result
       - simple_combo_without_history: Same 2-step calculation but only shows final result
       
    3. INDIVIDUAL TOOLS:
       - add_numbers: Add two numbers
       - multiply_by_two: Multiply by 2
       - subtract_ten: Subtract 10
       - square_number: Square a number
    
    BEHAVIOR DIFFERENCES:
    - return_history=True: Creates sub-agents for each step, shows all intermediate results and steps
    - return_history=False: Creates a single tool that executes all steps internally, only returns final result
    
    When asked to perform calculations:
    1. Explain which approach you're using (with/without history)
    2. Demonstrate the difference in output and behavior
    3. Show how intermediate steps are handled differently
    4. Compare efficiency and transparency trade-offs
    
    Example usage patterns:
    - "Calculate (5 + 3) × 2 - 10, then square it - show all steps"  → use with_history combo
    - "Calculate (5 + 3) × 2 - 10, then square it - final result only" → use without_history combo
    - "Compare the two approaches for the same calculation" → use both combos
    """,
    tools=[add_numbers, multiply_by_two, subtract_ten, square_number],
    tool_combos=[
        calculation_combo_with_history,
        calculation_combo_without_history,
        simple_combo_with_history,
        simple_combo_without_history
    ]
)
