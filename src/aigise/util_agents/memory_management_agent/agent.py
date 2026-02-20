import logging

from google.adk.models import BaseLlm
from google.adk.tools.agent_tool import AgentTool

from aigise.agents.aigise_agent import AigiseAgent
from aigise.memory.search_tool import search_memory
from aigise.toolbox.general.history_management import (
    get_all_events_for_summarization,
    get_all_invocations_for_agent,
    get_full_tool_res,
    get_full_tool_res_and_grep,
    list_all_events_for_session,
)
from aigise.toolbox.neo4j import (
    list_node_types,
    list_relations,
    run_neo4j_query,
)

logger = logging.getLogger(__name__)


MEMORY_MANAGEMENT_AGENT_INSTRUCTION = """
You are a Memory Management Agent that manages the memory of the system.
"""


def create_memory_management_agent(
    model: BaseLlm,
    name: str = "memory_management_agent",
) -> AigiseAgent:
    """
    Create a Memory Management Agent that manages the memory of the system.

    Args:
        model: Model to use for the agent.
        name: Name for the agent.

    Returns:
        AigiseAgent configured as a Memory Management Agent.
    """
    logger.info(f"Creating Memory Management Agent with name {name}")

    # Add memory management tools
    short_term_memory_tools = [
        get_all_invocations_for_agent,
        get_full_tool_res_and_grep,
        list_all_events_for_session,
        get_full_tool_res,
        get_all_events_for_summarization,
        run_neo4j_query,
        list_node_types,
        list_relations,
    ]
    long_term_memory_tools = [
        search_memory,
    ]
    all_tools = short_term_memory_tools + long_term_memory_tools

    # Create the agent
    agent = AigiseAgent(
        name=name,
        model=model,
        description="Memory management agent that manages the memory of the system. Use this tool when you need to manage the memory of the system.",
        instruction=MEMORY_MANAGEMENT_AGENT_INSTRUCTION,
        tools=all_tools,
    )

    return agent


def create_memory_management_agent_tool(
    model: BaseLlm,
    name: str = "memory_management_agent",
) -> AgentTool:
    """
    Create a Memory Management Agent wrapped as an AgentTool for use by other agents.
    """
    agent = create_memory_management_agent(
        model=model,
        name=name,
    )
    return AgentTool(agent=agent)
