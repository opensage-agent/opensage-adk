"""
Code Understanding Agent Module

Provides a Code Understanding Agent that caches Q&A pairs in Neo4j for improved
performance. The agent can be used as a tool (AgentTool) by any other agent.

Quick Start:
    from examples.agents.code_understanding_agent import create_code_understanding_agent_tool

    # Create code understanding agent tool
    code_tool = create_code_understanding_agent_tool(model)

    # Use in another agent
    orchestrator = AigiseAgent(
        name="orchestrator",
        tools=[code_tool],
        ...
    )
"""

# Re-export cache tools from aigise.toolbox.code_understanding for convenience
from aigise.toolbox.code_understanding import (
    cache_qa_pair,
    ensure_memory_indexes,
    get_cached_answer_by_id,
    list_cached_questions,
    lookup_similar_answers,
)

from .agent import (
    create_code_understanding_agent,
    create_code_understanding_agent_tool,
)

__all__ = [
    # Agent factories
    "create_code_understanding_agent",
    "create_code_understanding_agent_tool",
    # Cache tools (re-exported from aigise.toolbox.code_understanding)
    "list_cached_questions",
    "lookup_similar_answers",
    "get_cached_answer_by_id",
    "cache_qa_pair",
    "ensure_memory_indexes",
]
