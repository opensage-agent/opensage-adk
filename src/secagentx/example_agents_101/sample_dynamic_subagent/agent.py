from google.adk import Agent
from google.adk.models.lite_llm import LiteLlm
from secagentx.extended_features import get_dynamic_agent_manager, AgentStatus
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


async def create_subagent(
    agent_name: str,
    instruction: str,
    tools_list: List[str],
    description: Optional[str] = None
) -> Dict[str, Any]:
    """
    Dynamically create a sub-agent with specified tools and instructions.
    
    Args:
        agent_name: Custom name for the agent
        instruction: Custom instruction for the agent
        tools_list: List of tool names to assign to the agent
        description: Optional description for the agent
        
    Returns:
        Dictionary with creation result and agent details
    """
    try:
        manager = get_dynamic_agent_manager()
        
        # Available math tools mapping
        available_tools = {
            "add": add_numbers,
            "subtract": subtract_numbers, 
            "multiply": multiply_numbers,
            "divide": divide_numbers
        }
        
        # Validate tools
        tools_to_add = []
        invalid_tools = []
        
        for tool_name in tools_list:
            if tool_name in available_tools:
                tools_to_add.append(available_tools[tool_name])
            else:
                invalid_tools.append(tool_name)
        
        if invalid_tools:
            return {
                "success": False,
                "error": f"Invalid tools: {invalid_tools}. Available tools: {list(available_tools.keys())}"
            }
        
        # Create agent config with dynamic values
        config = {
            "type": "llm_agent",
            "name": agent_name,
            "instruction": instruction+"\nyou must use the tools provided to you to calculate the result.",
            "model": "anthropic/claude-sonnet-4-20250514",
            "description": description or f"Math agent {agent_name} with tools: {', '.join(tools_list)}"
        }
        
        # Create the sub-agent
        agent_id, agent_instance = await manager.create_agent(
            config,
            creator="math_root_agent"
        )
        
        # Add tools to the created agent instance
        if tools_to_add:
            agent_instance.tools.extend(tools_to_add)
        
        # Activate the sub-agent
        await manager.update_agent_status(agent_id, AgentStatus.ACTIVE)
        
        return {
            "success": True,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "tools_assigned": tools_list,
            "instruction": instruction,
            "description": config["description"],
            "message": f"Successfully created math agent '{agent_name}' with tools: {', '.join(tools_list)}"
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


async def list_active_agents() -> Dict[str, Any]:
    """List all active math sub-agents created by this root agent."""
    try:
        manager = get_dynamic_agent_manager()
        
        # Get all agents created by this root agent
        all_agents = manager.list_agents()
        active_agents = []
        
        for agent_metadata in all_agents:
            if agent_metadata.creator == "math_root_agent":
                # Get agent instance to check tools
                agent_instance = manager.get_agent(agent_metadata.id)
                tool_names = []
                if agent_instance and agent_instance.tools:
                    tool_names = [tool.__name__.replace('_numbers', '') for tool in agent_instance.tools if hasattr(tool, '__name__')]
                
                active_agents.append({
                    "agent_id": agent_metadata.id,
                    "name": agent_metadata.name,
                    "status": agent_metadata.status.value,
                    "created_at": agent_metadata.created_at.isoformat(),
                    "description": agent_metadata.description,
                    "tools": tool_names,
                    "model": agent_metadata.config.get('model', 'anthropic/claude-sonnet-4-20250514')
                })
        
        return {
            "success": True,
            "active_math_agents": active_agents,
            "total_count": len(active_agents)
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


async def call_subagent_as_tool(
    agent_name: str,
    task_message: str
) -> Dict[str, Any]:
    """
    Call a sub-agent as a tool - Agent as a Tool pattern.
    
    This treats the sub-agent as a specialized tool that can process
    natural language requests and return structured results.
    
    Args:
        agent_name: Name of the sub-agent to call
        task_message: Natural language task description
        
    Returns:
        Result from the sub-agent execution
    """
    try:
        manager = get_dynamic_agent_manager()
        
        # Find the target sub-agent
        all_agents = manager.list_agents()
        target_agent = None
        
        for agent_metadata in all_agents:
            if (agent_metadata.creator == "math_root_agent" and 
                agent_metadata.name == agent_name):
                target_agent = agent_metadata
                break
        
        if not target_agent:
            return {
                "success": False,
                "error": f"Sub-agent '{agent_name}' not found. Create one first."
            }
        
        # Get the agent instance
        agent_instance = manager.get_agent(target_agent.id)
        
        if not agent_instance:
            return {
                "success": False,
                "error": f"Failed to get agent instance for '{agent_name}'"
            }

        # Create user content in proper format
        content = types.Content(
            role='user',
            parts=[types.Part.from_text(text=task_message)]
        )
        
        # Create Runner with proper services
        runner = Runner(
            app_name=agent_instance.name,
            agent=agent_instance,
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
        )
        
        # Create session
        session = await runner.session_service.create_session(
            app_name=agent_instance.name,
            user_id='math_root_agent',
            state={}
        )
        
        # Execute sub-agent using Runner
        last_event = None
        async for event in runner.run_async(
            user_id=session.user_id, 
            session_id=session.id, 
            new_message=content
        ):
            last_event = event
        
        # Extract result
        if not last_event or not last_event.content or not last_event.content.parts:
            final_response = ''
        else:
            final_response = '\n'.join(p.text for p in last_event.content.parts if p.text)
        
        return {
            "success": True,
            "agent_id": target_agent.id,
            "agent_name": agent_name,
            "task_message": task_message,
            "response": final_response,
            "message": f"Sub-agent '{agent_name}' executed as tool successfully"
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to call sub-agent as tool: {str(e)}"
        }


root_agent = Agent(
    model=LiteLlm(model="anthropic/claude-sonnet-4-20250514"),
    name="math_root_agent",
    instruction="""
    You are a root math agent responsible for coordinating mathematical calculations through specialized sub-agents.
    
    Your capabilities include:
    1. Dynamically creating math sub-agents with custom names, instructions, and tools
    2. Managing and coordinating multiple math agents with different capabilities
    3. Delegating math tasks to appropriate sub-agents and executing calculations
    4. Aggregating results from multiple calculations
    
    When you receive a math request:
    1. Determine if you need to create a new specialized math agent
    2. Specify the agent's name, instruction, and required tools dynamically
    3. Delegate calculations to the most suitable agents
    4. Coordinate complex multi-step calculations
    5. Provide comprehensive mathematical results
    
    Available math tools:
    - add: For addition operations
    - subtract: For subtraction operations  
    - multiply: For multiplication operations
    - divide: For division operations
    
    Each sub-agent can be created with any combination of these tools and custom instructions.
    Use the available tools to create and manage math agents dynamically based on specific needs.
    """,
    description="Root math agent that dynamically creates and manages specialized math sub-agents for calculations.",
    tools=[
        create_subagent,
        list_active_agents,
        call_subagent_as_tool,
    ],
)
