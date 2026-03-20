"""Memory search tools for agents."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from google.adk.tools.tool_context import ToolContext

from opensage.memory.config import get_merged_domain
from opensage.memory.search.search_controller import MemorySearchController
from opensage.toolbox.sandbox_requirements import requires_sandbox
from opensage.utils.agent_utils import get_neo4j_client_from_context

logger = logging.getLogger(__name__)

# Singleton search controller
_search_controller: Optional[MemorySearchController] = None


def _get_search_controller() -> MemorySearchController:
    """Get or create the search controller singleton."""
    global _search_controller
    if _search_controller is None:
        # Try to get merged domain config
        try:
            domain = get_merged_domain("code", "qa")
        except Exception:
            domain = None
        _search_controller = MemorySearchController(
            domain_config=domain,
            max_iterations=3,
            use_llm_selection=True,
        )
    return _search_controller


@requires_sandbox("neo4j")
async def search_memory(
    query: str,
    *,
    node_types: Optional[List[str]] = None,
    max_results: int = 5,
    min_score: float = 0.5,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """
    Search the memory graph for relevant cached knowledge.

    Use this tool to find cached Q&A pairs, topics, or code entities
    that are semantically similar to your query.

    Args:
        query (str): The search query - describe what you're looking for.
        node_types (Optional[List[str]]): Optional list of node types to search. Defaults to
                   ["Question", "Topic"]. Other options: "Answer", "Function",
                   "Class", "File".
        max_results (int): Maximum number of results to return. Default is 5.
        min_score (float): Minimum similarity score (0-1). Default is 0.5.
    Returns:
        Dictionary with:
        - success: True if search completed
        - found: True if any results were found
        - results: List of matching items with scores
        - best_match: The highest scoring result (if any)
        - strategy_used: Which search strategy found results
    """
    client = await get_neo4j_client_from_context(tool_context, "memory")
    controller = _get_search_controller()

    result = await controller.search(
        query=query,
        node_types=node_types,
        client=client,
        max_results=max_results,
        min_score=min_score,
    )

    response = {
        "success": True,
        "found": result.has_results,
        "total_found": result.total_found,
        "strategy_used": result.strategy_used,
        "iterations": result.iterations,
        "results": [],
    }

    for item in result.items:
        response["results"].append(
            {
                "label": item.node_label,
                "score": item.score,
                "match_type": item.match_type,
                "text": item.get_display_text(),
                "properties": {
                    k: v
                    for k, v in item.properties.items()
                    if k not in ("embedding",) and not k.endswith("_hash")
                },
            }
        )

    if result.items:
        best = result.get_best_result()
        if best:
            response["best_match"] = {
                "label": best.node_label,
                "score": best.score,
                "text": best.get_display_text(),
            }

    logger.info(f"Memory search found {len(result.items)} results for: {query[:50]}...")
    return response
