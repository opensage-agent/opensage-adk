"""Generic Neo4j tools for querying and inspecting database structure."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from google.adk.tools.tool_context import ToolContext

from opensage.toolbox.sandbox_requirements import requires_sandbox
from opensage.utils.agent_utils import get_neo4j_client_from_context

logger = logging.getLogger(__name__)


@requires_sandbox("neo4j")
async def run_neo4j_query(
    *,
    tool_context: ToolContext,
    query: str,
    params: Optional[Dict[str, Any]] = None,
    database: str = "memory",
) -> Dict[str, Any]:
    """Execute an arbitrary Cypher query against Neo4j.

    Args:
      query (str): Cypher query string.
      params (Optional[Dict[str, Any]]): Optional parameters dict.
      database (str): Neo4j database type ("memory", "history", "analysis", ...)."""
    client = await get_neo4j_client_from_context(tool_context, database)
    records = await client.run_query(query, params or {})
    return {"success": True, "count": len(records or []), "records": records or []}


@requires_sandbox("neo4j")
async def list_node_types(
    *,
    database: str = "memory",
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """List all node types (labels) in the Neo4j database.

    This queries metadata only, so it's fast even on large databases.
    Use this to discover what node types exist in the database.

    Args:
        database (str): Neo4j database type ("memory", "history", "analysis", etc.). Default is "memory".
    Returns:
        Dictionary with:
        - success: True if query succeeded
        - node_types: List of node type names (labels)
        - count: Number of node types found
    """
    client = await get_neo4j_client_from_context(tool_context, database)

    query = "CALL db.labels() YIELD label RETURN label ORDER BY label"

    try:
        result = await client.run_query(query)

        node_types = [row["label"] for row in result] if result else []

        logger.info(f"Found {len(node_types)} node types in {database} database")
        return {
            "success": True,
            "node_types": node_types,
            "count": len(node_types),
        }
    except Exception as e:
        logger.error(f"Failed to list node types: {e}")
        return {
            "success": False,
            "error": f"Failed to list node types: {str(e)}",
            "node_types": [],
            "count": 0,
        }


@requires_sandbox("neo4j")
async def list_relations(
    *,
    database: str = "memory",
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """List all relationship types in the Neo4j database.

    This queries metadata only, so it's fast even on large databases.
    Use this to discover what relationship types exist in the database.

    Args:
        database (str): Neo4j database type ("memory", "history", "analysis", etc.). Default is "memory".
    Returns:
        Dictionary with:
        - success: True if query succeeded
        - relation_types: List of relationship type names
        - count: Number of relationship types found
    """
    client = await get_neo4j_client_from_context(tool_context, database)

    query = (
        "CALL db.relationshipTypes() YIELD relationshipType "
        "RETURN relationshipType ORDER BY relationshipType"
    )

    try:
        result = await client.run_query(query)

        relation_types = [row["relationshipType"] for row in result] if result else []

        logger.info(
            f"Found {len(relation_types)} relationship types in {database} database"
        )
        return {
            "success": True,
            "relation_types": relation_types,
            "count": len(relation_types),
        }
    except Exception as e:
        logger.error(f"Failed to list relationship types: {e}")
        return {
            "success": False,
            "error": f"Failed to list relationship types: {str(e)}",
            "relation_types": [],
            "count": 0,
        }
