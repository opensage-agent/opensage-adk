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
from aigise.toolbox.general.bash_tool import bash_tool
from aigise.toolbox.retrieval.search_tools import (
    get_line_around_linenum_in_file,
    grep_tool,
    list_functions_in_file,
)
from aigise.toolbox.static_analysis.cpg import search_function

logger = logging.getLogger(__name__)


CODE_UNDERSTANDING_AGENT_INSTRUCTION = """
You are a Code Understanding Agent that caches question-answer pairs for efficiency.

## Workflow for EVERY question:

1. FIRST, use `lookup_similar_answers` to find semantically similar cached answers.

2. If similar answers are found (cached=True):
   - Review the similar questions and their answers
   - If highly relevant (similarity > 0.85), return that answer directly
   - If partially relevant, use it as reference and augment with fresh research

3. If no similar answers exist:
   - Use your other available tools to thoroughly answer the question
   - After generating a complete answer, use `cache_qa_pair` to store it
   - Then return your answer to the user

4. Use `list_cached_questions` when you need to browse what's in the cache.

5. Use `get_cached_answer_by_id` to retrieve full answer content by ID.

IMPORTANT RULES:
- ALWAYS check for similar cached answers first before doing new work
- Cache successful answers to avoid redundant computation in the future
- For highly similar cached answers, reuse them directly
"""


def create_code_understanding_agent(
    model: BaseLlm,
    name: str = "code_understanding_agent",
) -> AigiseAgent:
    """
    Create a Code Understanding Agent with caching capabilities.

    This factory function creates a Code Understanding Agent that:
    1. Provides code analysis tools (search, grep, callers, etc.)
    2. Adds cache tools (lookup_similar_answers, cache_qa_pair)
    3. Uses caching logic to avoid redundant computations

    Args:
        model: Model to use for the agent.
        name: Name for the agent.

    Returns:
        AigiseAgent configured as a Code Understanding Agent.
    """
    logger.info(f"Creating Code Understanding Agent with name {name}")

    # Add cache tools
    cache_tools = [
        list_cached_questions,
        lookup_similar_answers,
        get_cached_answer_by_id,
        cache_qa_pair,
    ]
    code_tools = [
        search_function,
        grep_tool,
        list_functions_in_file,
        get_line_around_linenum_in_file,
        bash_tool,
    ]
    all_tools = cache_tools + code_tools

    # Create the agent
    agent = AigiseAgent(
        name=name,
        model=model,
        description="Code understanding agent that caches Q&A pairs. Use this tool when you have a specific question about the current project.",
        instruction=CODE_UNDERSTANDING_AGENT_INSTRUCTION,
        tools=all_tools,
    )

    return agent


def create_code_understanding_agent_tool(
    model: BaseLlm,
    name: str = "code_understanding_agent",
) -> AgentTool:
    """
    Create a Code Understanding Agent wrapped as an AgentTool for use by other agents.

    This is the primary entry point for using the Code Understanding Agent pattern.
    The returned AgentTool can be added to any agent's tools list.

    Args:
        model: Model to use for the agent.
        name: Name for the agent.

    Returns:
        AgentTool wrapping the Code Understanding Agent.

    Example:
        # Create the tool
        code_tool = create_code_understanding_agent_tool(model)

        # Use in another agent
        orchestrator = AigiseAgent(
            name="orchestrator",
            tools=[code_tool, other_tools...],
            ...
        )
    """
    agent = create_code_understanding_agent(
        name=name,
        model=model,
    )

    return AgentTool(agent=agent)
