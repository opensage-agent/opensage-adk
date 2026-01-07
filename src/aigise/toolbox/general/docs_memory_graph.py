"""Tools for ingesting documentation into Neo4j as a containment graph."""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from google.adk.tools.tool_context import ToolContext

from aigise.toolbox.decorators import requires_sandbox, safe_tool_execution
from aigise.utils.agent_utils import get_neo4j_client_from_context
from aigise.utils.project_info import find_path

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


def _read_text_file(path: Path, max_chars: int = 50_000) -> str:
    """Read a text file with a hard size cap to keep Neo4j payloads bounded."""
    try:
        content = path.read_text(errors="replace")
    except Exception as e:  # pylint: disable=broad-exception-caught
        return f"[read_error] {e!r}"

    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n\n[truncated]"


def _virtual_docs_paths(
    docs_root: Path,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Build DocNode nodes and CONTAINS relationships for a docs directory."""
    nodes: List[Dict[str, Any]] = []
    rels: List[Dict[str, str]] = []

    # Root node is always "/docs".
    nodes.append(
        {
            "path": "/docs",
            "name": "docs",
            "kind": "dir",
            "ext": "",
            "content": None,
        }
    )

    for path in sorted(docs_root.rglob("*")):
        rel = path.relative_to(docs_root)
        # Keep paths stable in Neo4j (always under /docs).
        virtual_path = "/docs" + ("" if str(rel) == "." else f"/{rel.as_posix()}")

        if path.is_dir():
            nodes.append(
                {
                    "path": virtual_path,
                    "name": path.name,
                    "kind": "dir",
                    "ext": "",
                    "content": None,
                }
            )
        else:
            nodes.append(
                {
                    "path": virtual_path,
                    "name": path.name,
                    "kind": "file",
                    "ext": path.suffix.lower(),
                    "content": _read_text_file(path),
                }
            )

        parent_virtual = "/docs"
        if rel.parent and str(rel.parent) != ".":
            parent_virtual = "/docs" + f"/{rel.parent.as_posix()}"
        rels.append({"parent": parent_virtual, "child": virtual_path})

    return nodes, rels


@safe_tool_execution
@requires_sandbox("neo4j")
async def ensure_docs_graph_indexes(*, tool_context: ToolContext) -> Dict[str, Any]:
    """Ensure DocNode indexes exist in the Neo4j 'memory' database."""
    client = await get_neo4j_client_from_context(tool_context, "memory")
    try:
        await client.run_query(
            "CREATE INDEX docnode_path IF NOT EXISTS FOR (d:DocNode) ON (d.path)"
        )
        await client.run_query(
            "CREATE INDEX docnode_name IF NOT EXISTS FOR (d:DocNode) ON (d.name)"
        )
        return {"success": True, "message": "DocNode indexes ensured"}
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Let safe_tool_execution handle formatting, but keep a consistent shape.
        raise RuntimeError(f"Failed to ensure DocNode indexes: {e}") from e


@safe_tool_execution
@requires_sandbox("neo4j")
async def ingest_docs_to_neo4j(
    *,
    tool_context: ToolContext,
    docs_path: str = "docs",
    ensure_indexes: bool = True,
) -> Dict[str, Any]:
    """Ingest a docs folder into Neo4j as DocNode nodes + CONTAINS edges.

    This uses the Neo4j client type "memory" so that documentation graphs live in
    the dedicated memory database.

    Args:
      tool_context: ADK tool context.
      docs_path: Path to docs folder. If relative, resolved via `find_path(...)`.
      ensure_indexes: Whether to ensure basic indexes before ingesting.

    Returns:
      Dictionary with counts and a short status message.
    """
    root = Path(docs_path)
    if not root.is_absolute():
        root = find_path(docs_path)

    if not root.exists() or not root.is_dir():
        raise ValueError(f"docs_path must be an existing directory, got: {root}")

    nodes, rels = _virtual_docs_paths(root)
    logger.info("Ingesting %d docs nodes and %d relations", len(nodes), len(rels))

    client = await get_neo4j_client_from_context(tool_context, "memory")
    if ensure_indexes:
        await ensure_docs_graph_indexes(tool_context=tool_context)

    timestamp = _now_iso()

    upsert_nodes_query = """
UNWIND $nodes AS n
MERGE (d:DocNode {path: n.path})
ON CREATE SET d.created_at = $timestamp,
              d.access_count = 0
SET d.name = n.name,
    d.kind = n.kind,
    d.ext = n.ext,
    d.content = n.content,
    d.updated_at = $timestamp
"""
    upsert_rels_query = """
UNWIND $rels AS r
MATCH (p:DocNode {path: r.parent})
MATCH (c:DocNode {path: r.child})
MERGE (p)-[:CONTAINS]->(c)
"""

    # Keep payloads moderate; batch if needed.
    batch_size = 200
    for i in range(0, len(nodes), batch_size):
        await client.run_query(
            upsert_nodes_query,
            {"nodes": nodes[i : i + batch_size], "timestamp": timestamp},
        )
    for i in range(0, len(rels), batch_size):
        await client.run_query(upsert_rels_query, {"rels": rels[i : i + batch_size]})

    return {
        "success": True,
        "root": "/docs",
        "docs_path": str(root),
        "node_count": len(nodes),
        "rel_count": len(rels),
        "message": (
            "Ingested docs into Neo4j (db=memory): "
            f"{len(nodes)} nodes, {len(rels)} CONTAINS relationships."
        ),
    }


@safe_tool_execution
@requires_sandbox("neo4j")
async def get_doc_node(
    path: str,
    *,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Get a single DocNode by its full repo path key."""
    client = await get_neo4j_client_from_context(tool_context, "memory")
    timestamp = _now_iso()

    query = """
  MATCH (d:DocNode {path: $path})
  SET d.last_accessed = $timestamp,
      d.access_count = COALESCE(d.access_count, 0) + 1
  RETURN d.path as path,
         d.name as name,
         d.kind as kind,
         d.ext as ext,
         d.content as content,
         d.created_at as created_at,
         d.updated_at as updated_at,
         d.access_count as access_count,
         d.last_accessed as last_accessed
  """
    result = await client.run_query(query, {"path": path, "timestamp": timestamp})
    if not result:
        return {"success": True, "found": False, "path": path}
    return {"success": True, "found": True, "item": result[0]}


@safe_tool_execution
@requires_sandbox("neo4j")
async def search_doc_nodes(
    query: str,
    *,
    tool_context: ToolContext,
    limit: int = 50,
    offset: int = 0,
    kind: Optional[str] = None,
    search_in_content: bool = False,
) -> Dict[str, Any]:
    """Search DocNodes by path/name (and optionally content), with pagination."""
    client = await get_neo4j_client_from_context(tool_context, "memory")

    clauses: List[str] = []
    params: Dict[str, Any] = {"query": query, "limit": limit, "offset": offset}

    if query.startswith("/"):
        clauses.append("d.path STARTS WITH $query")
    else:
        # Always search name; optionally include path/content.
        text_clauses = ["d.name CONTAINS $query", "d.path CONTAINS $query"]
        if search_in_content:
            text_clauses.append("d.content CONTAINS $query")
        clauses.append("(" + " OR ".join(text_clauses) + ")")

    if kind:
        clauses.append("d.kind = $kind")
        params["kind"] = kind

    where_sql = " AND ".join(clauses) if clauses else "TRUE"

    count_query = f"""
  MATCH (d:DocNode)
  WHERE {where_sql}
  RETURN count(d) as total
  """
    total_result = await client.run_query(count_query, params)
    total = total_result[0]["total"] if total_result else 0

    list_query = f"""
  MATCH (d:DocNode)
  WHERE {where_sql}
  RETURN d.path as path,
         d.name as name,
         d.kind as kind,
         d.ext as ext,
         d.created_at as created_at,
         d.updated_at as updated_at,
         d.access_count as access_count,
         d.last_accessed as last_accessed
  ORDER BY d.path ASC
  SKIP $offset
  LIMIT $limit
  """
    items = await client.run_query(list_query, params)
    return {
        "success": True,
        "total": total,
        "items": items or [],
        "limit": limit,
        "offset": offset,
    }


@safe_tool_execution
@requires_sandbox("neo4j")
async def create_doc_node(
    *,
    tool_context: ToolContext,
    path: str,
    name: str,
    kind: str,
    ext: str = "",
    content: Optional[str] = None,
    parent_path: Optional[str] = None,
    link_to_parent: bool = True,
) -> Dict[str, Any]:
    """Create (or upsert) a DocNode by path, optionally linking it to a parent."""
    client = await get_neo4j_client_from_context(tool_context, "memory")
    timestamp = _now_iso()

    upsert_query = """
  MERGE (d:DocNode {path: $path})
  ON CREATE SET d.created_at = $timestamp,
                d.access_count = 0
  SET d.name = $name,
      d.kind = $kind,
      d.ext = $ext,
      d.content = $content,
      d.updated_at = $timestamp
  RETURN d.path as path,
         d.name as name,
         d.kind as kind,
         d.ext as ext,
         d.content as content,
         d.created_at as created_at,
         d.updated_at as updated_at,
         d.access_count as access_count,
         d.last_accessed as last_accessed,
         CASE WHEN d.created_at = $timestamp THEN 'created' ELSE 'updated' END as action
  """

    result = await client.run_query(
        upsert_query,
        {
            "path": path,
            "name": name,
            "kind": kind,
            "ext": ext,
            "content": content,
            "timestamp": timestamp,
        },
    )
    item = result[0] if result else {}
    action = item.get("action", "updated")

    if parent_path and link_to_parent:
        # Ensure parent exists, then link parent -> child.
        await client.run_query(
            """
      MERGE (p:DocNode {path: $parent_path})
      ON CREATE SET p.created_at = $timestamp,
                    p.access_count = 0
      SET p.updated_at = $timestamp
      WITH p
      MATCH (c:DocNode {path: $child_path})
      MERGE (p)-[:CONTAINS]->(c)
      """,
            {"parent_path": parent_path, "child_path": path, "timestamp": timestamp},
        )

    item.pop("action", None)
    return {
        "success": True,
        "action": action,
        "item": item,
        "message": f"DocNode {action}: {path}",
    }


@safe_tool_execution
@requires_sandbox("neo4j")
async def update_doc_node(
    *,
    tool_context: ToolContext,
    path: str,
    patch: Dict[str, Any],
) -> Dict[str, Any]:
    """Update a DocNode by path (whitelisted fields only)."""
    allowed_keys = {"name", "kind", "ext", "content"}
    sanitized_patch = {k: v for k, v in patch.items() if k in allowed_keys}
    if not sanitized_patch:
        return {
            "success": False,
            "error": f"No valid fields to update. Allowed keys: {sorted(allowed_keys)}",
        }

    client = await get_neo4j_client_from_context(tool_context, "memory")
    timestamp = _now_iso()

    query = """
  MATCH (d:DocNode {path: $path})
  SET d += $patch,
      d.updated_at = $timestamp
  RETURN d.path as path,
         d.name as name,
         d.kind as kind,
         d.ext as ext,
         d.content as content,
         d.created_at as created_at,
         d.updated_at as updated_at,
         d.access_count as access_count,
         d.last_accessed as last_accessed
  """
    result = await client.run_query(
        query, {"path": path, "patch": sanitized_patch, "timestamp": timestamp}
    )
    if not result:
        return {"success": True, "found": False, "path": path}
    return {
        "success": True,
        "found": True,
        "item": result[0],
        "message": f"Updated DocNode: {path}",
    }


@safe_tool_execution
@requires_sandbox("neo4j")
async def run_neo4j_query(
    *,
    tool_context: ToolContext,
    query: str,
    params: Optional[Dict[str, Any]] = None,
    client_type: str = "memory",
) -> Dict[str, Any]:
    """Execute an arbitrary Cypher query against Neo4j.

    Args:
      tool_context: ADK tool context.
      query: Cypher query string.
      params: Optional parameters dict.
      client_type: Neo4j client type/database ("memory", "history", "analysis", ...).
    """
    client = await get_neo4j_client_from_context(tool_context, client_type)
    records = await client.run_query(query, params or {})
    return {"success": True, "count": len(records or []), "records": records or []}
