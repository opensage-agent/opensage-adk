from aigise.extended_features.sec_agent import SecAgent
from google.adk.models.lite_llm import LiteLlm
from aigise.extended_features import get_dynamic_agent_manager, AgentStatus
from aigise.toolbox.general.dynamic_subagent import create_subagent, list_active_agents, call_subagent_as_tool
from typing import Dict, Any, Optional, List
from google.adk.runners import Runner
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types


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
        "status": "completed"
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
        "status": "completed"
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
            "status": "error"
        }
    
    result = a / b
    return {
        "operation": "division",
        "operand_a": a,
        "operand_b": b,
        "result": result,
        "formula": f"{a} ÷ {b} = {result}",
        "status": "completed"
    }

root_agent = SecAgent(
    model=LiteLlm(model="anthropic/claude-sonnet-4-20250514"),
    name="math_root_agent",
    instruction="""
    You are a root math agent responsible for coordinating mathematical calculations through specialized sub-agents.
    
    Your capabilities include:
    1. Dynamically creating math sub-agents with custom names, instructions, and tools
    2. Managing and coordinating multiple math agents with different capabilities
    3. Delegating math tasks to appropriate sub-agents and executing calculations
    4. Aggregating results from multiple calculations

    You should not calculate the result yourself, you should delegate the calculation to the sub-agents. If a sub-agent cannot perform the calculation, you should create a new sub-agent with the appropriate tools and instructions.
    
    When you receive a math request:
    1. Determine if you need to create a new specialized math agent
    2. Specify the agent's name, instruction, and required tools dynamically
    3. Delegate calculations to the most suitable agents
    4. Coordinate complex multi-step calculations
    5. Provide comprehensive mathematical results
    
    Use the available tools to create and manage math agents dynamically based on specific needs.
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
