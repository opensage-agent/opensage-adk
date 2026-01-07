import logging

from google.adk.models import BaseLlm
from google.adk.tools.agent_tool import AgentTool

from aigise.agents.aigise_agent import AigiseAgent
from aigise.toolbox.code_understanding import (
    cache_qa_pair,
    get_cached_answer_by_id,
    list_cached_questions,
    lookup_similar_answers,
)
from aigise.toolbox.general.docs_memory_graph import (
    create_doc_node,
    ensure_docs_graph_indexes,
    get_doc_node,
    ingest_docs_to_neo4j,
    run_neo4j_query,
    search_doc_nodes,
    update_doc_node,
)
from aigise.toolbox.general.history_management import (
    get_all_events_for_summarization,
    get_all_invocations_for_agent,
    get_full_tool_res,
    get_full_tool_res_and_grep,
    list_all_events_for_session,
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
        ensure_docs_graph_indexes,
        ingest_docs_to_neo4j,
        get_doc_node,
        search_doc_nodes,
        create_doc_node,
        update_doc_node,
        run_neo4j_query,
    ]
    long_term_memory_tools = [
        list_cached_questions,
        lookup_similar_answers,
        get_cached_answer_by_id,
        cache_qa_pair,
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
