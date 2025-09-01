from typing import Dict, Any


def extract_tools_from_agent(agent) -> Dict[str, Any]:
    """Extract all tools from an agent instance and create a name->tool mapping.
    
    Args:
        agent: Agent instance to extract tools from
        
    Returns:
        Dictionary mapping tool names to tool objects
    """
    available_tools = {}
    
    if hasattr(agent, 'tools') and agent.tools:
        for tool in agent.tools:
            tool_name = None
            tool_obj = None
            
            if hasattr(tool, 'name'):
                tool_name = tool.name
                tool_obj = tool
            elif hasattr(tool, '__name__'):
                tool_name = tool.__name__
                tool_obj = tool
            elif hasattr(tool, 'func') and hasattr(tool.func, '__name__'):
                tool_name = tool.func.__name__
                tool_obj = tool.func
            elif callable(tool):
                tool_name = getattr(tool, '__name__', str(tool))
                tool_obj = tool
            
            if tool_name and tool_obj:
                available_tools[tool_name] = tool_obj
    
    return available_tools
