"""Memory search tools for agents."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from google.adk.tools.tool_context import ToolContext

from aigise.memory.config import get_merged_domain
from aigise.memory.search.search_controller import MemorySearchController
from aigise.toolbox.decorators import requires_sandbox, safe_tool_execution
from aigise.utils.agent_utils import get_neo4j_client_from_context

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


@safe_tool_execution
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
        query: The search query - describe what you're looking for.
        node_types: Optional list of node types to search. Defaults to
                   ["Question", "Topic"]. Other options: "Answer", "Function",
                   "Class", "File".
        max_results: Maximum number of results to return. Default is 5.
        min_score: Minimum similarity score (0-1). Default is 0.5.

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


@safe_tool_execution
@requires_sandbox("neo4j")
async def get_related_entities(
    node_label: str,
    node_key: Dict[str, Any],
    *,
    relationship_types: Optional[List[str]] = None,
    direction: str = "both",
    max_results: int = 10,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """
    Get entities related to a specific node in the memory graph.

    Use this to explore connections between cached knowledge,
    such as finding all topics related to a question or all
    content that references a specific topic.

    Args:
        node_label: Label of the node to find relations for (e.g., "Topic").
        node_key: Properties to identify the node (e.g., {"name": "authentication"}).
        relationship_types: Optional list of relationship types to follow.
                           Options: "ABOUT", "HAS_ANSWER", "HAS_TOPIC", "RELATED_TO", "MENTIONS".
        direction: Direction to traverse relationships:
                  - "outgoing": source -> target (what this node points to)
                  - "incoming": source <- target (what points to this node)
                  - "both": both directions (default)
        max_results: Maximum number of results. Default is 10.

    Returns:
        Dictionary with:
        - success: True if query completed
        - source_found: True if the source node exists
        - relationships: Dict mapping relationship type to list of related nodes

    Example:
        # Find all Text/Question nodes that have the "authentication" topic
        get_related_entities(
            node_label="Topic",
            node_key={"name": "authentication"},
            relationship_types=["HAS_TOPIC"],
            direction="incoming",  # Text --[HAS_TOPIC]--> Topic
        )
    """
    client = await get_neo4j_client_from_context(tool_context, "memory")

    # Build match conditions for source node
    where_parts = []
    params = {}
    for i, (key, value) in enumerate(node_key.items()):
        param_name = f"k{i}"
        where_parts.append(f"source.{key} = ${param_name}")
        params[param_name] = value

    if not where_parts:
        return {
            "success": False,
            "error": "node_key must contain at least one property",
        }

    # Build relationship pattern
    rel_filter = ""
    if relationship_types:
        rel_filter = ":" + "|".join(relationship_types)

    # Build direction-aware relationship pattern
    if direction == "outgoing":
        rel_pattern = f"(source)-[r{rel_filter}]->(target)"
    elif direction == "incoming":
        rel_pattern = f"(source)<-[r{rel_filter}]-(target)"
    else:  # both
        rel_pattern = f"(source)-[r{rel_filter}]-(target)"

    query = f"""
    MATCH (source:{node_label})
    WHERE {" AND ".join(where_parts)}
    OPTIONAL MATCH {rel_pattern}
    RETURN type(r) as rel_type,
           labels(target) as target_labels,
           properties(target) as target_props
    LIMIT $limit
    """
    params["limit"] = max_results

    try:
        result = await client.run_query(query, params)
    except Exception as e:
        logger.error(f"Failed to get related entities: {e}")
        return {"success": False, "error": str(e)}

    # Organize by relationship type
    relationships: Dict[str, List[Dict]] = {}
    for row in result or []:
        rel_type = row.get("rel_type")
        if rel_type is None:
            continue

        if rel_type not in relationships:
            relationships[rel_type] = []

        target_labels = row.get("target_labels", [])
        target_props = row.get("target_props", {})

        # Clean up properties (remove embeddings)
        clean_props = {
            k: v
            for k, v in target_props.items()
            if k not in ("embedding",) and not k.endswith("_hash")
        }

        relationships[rel_type].append(
            {
                "label": target_labels[0] if target_labels else "Unknown",
                "properties": clean_props,
            }
        )

    return {
        "success": True,
        "source_found": len(result or []) > 0,
        "source": {"label": node_label, "key": node_key},
        "relationships": relationships,
    }


@safe_tool_execution
@requires_sandbox("neo4j")
async def list_memory_contents(
    *,
    node_types: Optional[List[str]] = None,
    limit: int = 20,
    offset: int = 0,
    order_by: str = "created_at",
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """
    List contents of the memory graph by node type.

    Use this to browse what's in memory without a specific search query.
    Useful for exploring available cached knowledge.

    Args:
        node_types: Node types to list. Defaults to ["Question", "Topic"].
        limit: Maximum items per type. Default is 20.
        offset: Number of items to skip (for pagination). Default is 0.
        order_by: Property to order by. Default is "created_at".

    Returns:
        Dictionary with:
        - success: True if query completed
        - contents: Dict mapping node type to list of items
        - totals: Dict mapping node type to total count
    """
    client = await get_neo4j_client_from_context(tool_context, "memory")

    if node_types is None:
        node_types = ["Question", "Topic"]

    contents: Dict[str, List[Dict]] = {}
    totals: Dict[str, int] = {}

    for node_type in node_types:
        # Get count
        count_query = f"MATCH (n:{node_type}) RETURN count(n) as total"
        try:
            count_result = await client.run_query(count_query)
            totals[node_type] = count_result[0]["total"] if count_result else 0
        except Exception:
            totals[node_type] = 0

        # Get items
        # Determine display property based on type
        display_props = {
            "Question": "text",
            "Answer": "text",
            "Topic": "name",
            "Function": "name",
            "Class": "name",
            "File": "path",
        }
        display_prop = display_props.get(node_type, "name")

        query = f"""
        MATCH (n:{node_type})
        RETURN properties(n) as props
        ORDER BY n.{order_by} DESC
        SKIP $offset
        LIMIT $limit
        """

        try:
            result = await client.run_query(query, {"limit": limit, "offset": offset})

            items = []
            for row in result or []:
                props = row.get("props", {})
                # Clean up properties
                clean_props = {
                    k: v
                    for k, v in props.items()
                    if k not in ("embedding",) and not k.endswith("_hash")
                }
                items.append(
                    {
                        "display": props.get(display_prop, ""),
                        "properties": clean_props,
                    }
                )

            contents[node_type] = items

        except Exception as e:
            logger.warning(f"Failed to list {node_type}: {e}")
            contents[node_type] = []

    return {
        "success": True,
        "contents": contents,
        "totals": totals,
        "limit": limit,
        "offset": offset,
    }


@safe_tool_execution
@requires_sandbox("neo4j")
async def get_entity_by_id(
    node_label: str,
    node_key: Dict[str, Any],
    *,
    include_relationships: bool = False,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """
    Get a specific entity from memory by its identifier.

    Args:
        node_label: Label of the node (e.g., "Question", "Answer").
        node_key: Properties to identify the node (e.g., {"answer_id": "..."}).
        include_relationships: Whether to include connected entities.

    Returns:
        Dictionary with:
        - success: True if query completed
        - found: True if the entity exists
        - entity: The entity properties (if found)
        - relationships: Related entities (if include_relationships=True)
    """
    client = await get_neo4j_client_from_context(tool_context, "memory")

    # Build match conditions
    where_parts = []
    params = {}
    for i, (key, value) in enumerate(node_key.items()):
        param_name = f"k{i}"
        where_parts.append(f"n.{key} = ${param_name}")
        params[param_name] = value

    if not where_parts:
        return {
            "success": False,
            "error": "node_key must contain at least one property",
        }

    query = f"""
    MATCH (n:{node_label})
    WHERE {" AND ".join(where_parts)}
    RETURN properties(n) as props
    LIMIT 1
    """

    try:
        result = await client.run_query(query, params)

        if not result or len(result) == 0:
            return {
                "success": True,
                "found": False,
                "label": node_label,
                "key": node_key,
            }

        props = result[0]["props"]
        # Clean up properties
        clean_props = {k: v for k, v in props.items() if k not in ("embedding",)}

        response = {
            "success": True,
            "found": True,
            "label": node_label,
            "entity": clean_props,
        }

        # Optionally include relationships
        if include_relationships:
            rel_result = await get_related_entities(
                node_label=node_label,
                node_key=node_key,
                tool_context=tool_context,
            )
            if rel_result.get("success"):
                response["relationships"] = rel_result.get("relationships", {})

        return response

    except Exception as e:
        logger.error(f"Failed to get entity: {e}")
        return {"success": False, "error": str(e)}
