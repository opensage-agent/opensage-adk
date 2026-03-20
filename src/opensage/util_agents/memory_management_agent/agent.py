import logging

from google.adk.models import BaseLlm
from google.adk.tools.agent_tool import AgentTool

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.memory.search_tool import search_memory
from opensage.toolbox.general.history_management import (
    get_all_events_for_summarization,
    get_all_invocations_for_agent,
    get_full_tool_res,
    get_full_tool_res_and_grep,
    list_all_events_for_session,
)
from opensage.toolbox.neo4j import (
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
) -> OpenSageAgent:
    """
    Create a Memory Management Agent that manages the memory of the system.

    Args:
        model (BaseLlm): Model to use for the agent.
        name (str): Name for the agent.
    Returns:
        OpenSageAgent: OpenSageAgent configured as a Memory Management Agent.
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
    agent = OpenSageAgent(
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
