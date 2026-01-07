"""
Code understanding tools with Q&A caching in Neo4j.

These tools enable the Code Understanding Agent to store and retrieve
question-answer pairs from a dedicated Neo4j "memory" database. Supports
both exact match and vector similarity search using Gemini embeddings.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from google.adk.tools.tool_context import ToolContext

from aigise.toolbox.decorators import safe_tool_execution
from aigise.utils.agent_utils import (
    get_aigise_session_id_from_context,
    get_neo4j_client_from_context,
)

logger = logging.getLogger(__name__)

# Embedding dimension for gemini-embedding-001
EMBEDDING_DIMENSION = 3072

# Track whether indexes have been initialized (lazy init)
_indexes_initialized = False


async def _generate_embedding(text: str) -> List[float]:
    """Generate embedding for text using Gemini embedding model.

    Args:
        text: Text to generate embedding for.

    Returns:
        List of floats representing the embedding vector.
    """
    from google import genai

    client = genai.Client()
    result = await client.aio.models.embed_content(
        model="gemini-embedding-001",
        contents=text,
    )
    return result.embeddings[0].values


def _hash_question(question: str) -> str:
    """Generate SHA256 hash of question for fast lookup."""
    return hashlib.sha256(question.strip().encode("utf-8")).hexdigest()


async def _ensure_indexes_once(tool_context: ToolContext) -> None:
    """Lazily ensure indexes are created (called once on first cache operation)."""
    global _indexes_initialized
    if _indexes_initialized:
        return

    try:
        await ensure_memory_indexes(tool_context)
        _indexes_initialized = True
    except Exception as e:
        logger.warning(f"Failed to initialize indexes (will retry later): {e}")


@safe_tool_execution
async def list_cached_questions(
    *,
    tool_context: ToolContext,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    List all cached questions with their IDs.

    Use this tool to browse available cached Q&A pairs. Returns question
    titles and IDs that can be used with get_cached_answer_by_id.

    Args:
        limit: Maximum number of results to return. Default is 50.
        offset: Number of results to skip for pagination. Default is 0.

    Returns:
        Dictionary with:
        - success: True if query succeeded
        - total: Total number of cached Q&A pairs
        - items: List of {qa_id, question, cached_at, access_count}
    """
    client = await get_neo4j_client_from_context(tool_context, "memory")

    # Get total count
    count_query = "MATCH (q:QACache) RETURN count(q) as total"
    count_result = await client.run_query(count_query)
    total = count_result[0]["total"] if count_result else 0

    # Get paginated list
    query = """
    MATCH (q:QACache)
    RETURN q.qa_id as qa_id,
           q.question as question,
           q.created_at as cached_at,
           q.access_count as access_count
    ORDER BY q.created_at DESC
    SKIP $offset
    LIMIT $limit
    """

    result = await client.run_query(query, {"limit": limit, "offset": offset})

    items = (
        [
            {
                "qa_id": row["qa_id"],
                "question": row["question"],
                "cached_at": row["cached_at"],
                "access_count": row["access_count"],
            }
            for row in result
        ]
        if result
        else []
    )

    logger.info(f"Listed {len(items)} cached questions (total: {total})")
    return {
        "success": True,
        "total": total,
        "items": items,
        "limit": limit,
        "offset": offset,
    }


@safe_tool_execution
async def get_cached_answer_by_id(
    qa_id: str,
    *,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """
    Get detailed information for a cached Q&A pair by its ID.

    Use this after list_cached_questions or lookup_similar_answers to get
    the full answer content.

    Args:
        qa_id: The unique identifier of the cached Q&A pair.

    Returns:
        Dictionary with:
        - success: True if found
        - found: True if the Q&A pair exists
        - question: The original question
        - answer: The cached answer
        - answering_agent: Agent that generated the answer
        - answering_model: Model used
        - cached_at: When it was cached
        - access_count: Number of times accessed
    """
    client = await get_neo4j_client_from_context(tool_context, "memory")
    timestamp = datetime.now().isoformat()

    query = """
    MATCH (q:QACache {qa_id: $qa_id})
    SET q.last_accessed = $timestamp, q.access_count = q.access_count + 1
    RETURN q.question as question,
           q.answer as answer,
           q.answering_agent as agent,
           q.answering_model as model,
           q.created_at as cached_at,
           q.access_count as access_count,
           q.metadata as metadata
    """

    result = await client.run_query(query, {"qa_id": qa_id, "timestamp": timestamp})

    if result and len(result) > 0:
        row = result[0]
        logger.info(f"Retrieved cached answer for qa_id={qa_id}")
        return {
            "success": True,
            "found": True,
            "qa_id": qa_id,
            "question": row["question"],
            "answer": row["answer"],
            "answering_agent": row["agent"],
            "answering_model": row["model"],
            "cached_at": row["cached_at"],
            "access_count": row["access_count"],
            "metadata": row["metadata"],
        }

    logger.info(f"No cached answer found for qa_id={qa_id}")
    return {"success": True, "found": False, "qa_id": qa_id}


@safe_tool_execution
async def lookup_similar_answers(
    question: str,
    *,
    top_k: int = 3,
    similarity_threshold: float = 0.7,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """
    Look up similar cached answers using vector similarity search.

    Use this when exact match fails but you want to find semantically similar
    questions that have been answered before.

    Args:
        question: The question to search for similar matches.
        top_k: Number of similar results to return. Default is 3.
        similarity_threshold: Minimum similarity score (0-1) to consider a match.
                              Default is 0.7.

    Returns:
        Dictionary with:
        - cached: True if similar answers were found, False otherwise
        - results: List of similar Q&A pairs with similarity scores
        - top_answer: The most similar answer (if any results found)
        - top_similarity: Similarity score of the top answer
    """
    # Ensure indexes exist (lazy initialization on first use)
    await _ensure_indexes_once(tool_context)

    client = await get_neo4j_client_from_context(tool_context, "memory")
    timestamp = datetime.now().isoformat()

    # Generate embedding for the query
    try:
        query_embedding = await _generate_embedding(question)
    except Exception as e:
        logger.warning(f"Failed to generate embedding: {e}")
        return {
            "success": False,
            "cached": False,
            "error": f"Failed to generate embedding: {e}",
        }

    # Use Neo4j vector similarity search
    query = """
    CALL db.index.vector.queryNodes('qa_embedding_index', $top_k, $embedding)
    YIELD node, score
    WHERE score >= $threshold
    SET node.last_accessed = $timestamp, node.access_count = node.access_count + 1
    RETURN node.question as question,
           node.answer as answer,
           node.answering_agent as agent,
           node.answering_model as model,
           node.access_count as hits,
           node.created_at as cached_at,
           score as similarity
    ORDER BY score DESC
    """

    try:
        result = await client.run_query(
            query,
            {
                "embedding": query_embedding,
                "top_k": top_k,
                "threshold": similarity_threshold,
                "timestamp": timestamp,
            },
        )
    except Exception as e:
        logger.warning(f"Vector search failed (index may not exist): {e}")
        return {
            "success": False,
            "cached": False,
            "error": f"Vector search failed: {e}. Ensure vector index exists.",
        }

    if result and len(result) > 0:
        results = [
            {
                "question": row["question"],
                "answer": row["answer"],
                "answering_agent": row["agent"],
                "answering_model": row["model"],
                "cache_hits": row["hits"],
                "cached_at": row["cached_at"],
                "similarity": row["similarity"],
            }
            for row in result
        ]
        logger.info(f"Found {len(results)} similar cached answers")
        return {
            "success": True,
            "cached": True,
            "results": results,
            "top_answer": results[0]["answer"],
            "top_similarity": results[0]["similarity"],
        }

    logger.info("No similar answers found")
    return {"success": True, "cached": False, "results": []}


@safe_tool_execution
async def cache_qa_pair(
    question: str,
    answer: str,
    answering_agent: str,
    answering_model: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    store_embedding: bool = True,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """
    Cache a question-answer pair in Neo4j for future retrieval.

    Use this tool AFTER successfully answering a question that was not in the cache.
    This allows future identical questions to be answered immediately from cache.
    Also generates and stores embeddings for similarity search.

    Args:
        question: The question text that was asked.
        answer: The answer text that was generated.
        answering_agent: Name of the agent that generated the answer.
        answering_model: Model identifier used to generate the answer.
        metadata: Optional additional metadata to store with the cache entry.
        store_embedding: Whether to generate and store embedding for similarity
                         search. Default is True.

    Returns:
        Dictionary with:
        - success: True if caching succeeded
        - qa_id: Unique identifier for the cached entry
        - action: "created" for new entries, "updated" for existing ones
        - embedding_stored: Whether embedding was stored successfully
    """
    # Ensure indexes exist (lazy initialization on first use)
    await _ensure_indexes_once(tool_context)

    client = await get_neo4j_client_from_context(tool_context, "memory")
    aigise_session_id = get_aigise_session_id_from_context(tool_context)

    qa_id = str(uuid.uuid4())
    question_hash = _hash_question(question)
    timestamp = datetime.now().isoformat()

    # Generate embedding if requested
    embedding = None
    embedding_error = None
    if store_embedding:
        try:
            embedding = await _generate_embedding(question)
        except Exception as e:
            logger.warning(f"Failed to generate embedding, continuing without it: {e}")
            embedding_error = str(e)

    # Build query based on whether we have an embedding
    if embedding is not None:
        query = """
        MERGE (q:QACache {question_hash: $question_hash, question: $question})
        ON CREATE SET
            q.qa_id = $qa_id,
            q.answer = $answer,
            q.answering_agent = $answering_agent,
            q.answering_model = $answering_model,
            q.created_at = $created_at,
            q.last_accessed = $created_at,
            q.access_count = 0,
            q.aigise_session_id = $aigise_session_id,
            q.metadata = $metadata,
            q.embedding = $embedding
        ON MATCH SET
            q.answer = $answer,
            q.answering_agent = $answering_agent,
            q.answering_model = $answering_model,
            q.last_accessed = $created_at,
            q.embedding = $embedding
        RETURN q.qa_id as qa_id,
               CASE WHEN q.created_at = $created_at THEN 'created' ELSE 'updated' END as action
        """
        params = {
            "qa_id": qa_id,
            "question": question.strip(),
            "question_hash": question_hash,
            "answer": answer,
            "answering_agent": answering_agent,
            "answering_model": answering_model,
            "created_at": timestamp,
            "aigise_session_id": aigise_session_id,
            "metadata": json.dumps(metadata or {}),
            "embedding": embedding,
        }
    else:
        query = """
        MERGE (q:QACache {question_hash: $question_hash, question: $question})
        ON CREATE SET
            q.qa_id = $qa_id,
            q.answer = $answer,
            q.answering_agent = $answering_agent,
            q.answering_model = $answering_model,
            q.created_at = $created_at,
            q.last_accessed = $created_at,
            q.access_count = 0,
            q.aigise_session_id = $aigise_session_id,
            q.metadata = $metadata
        ON MATCH SET
            q.answer = $answer,
            q.answering_agent = $answering_agent,
            q.answering_model = $answering_model,
            q.last_accessed = $created_at
        RETURN q.qa_id as qa_id,
               CASE WHEN q.created_at = $created_at THEN 'created' ELSE 'updated' END as action
        """
        params = {
            "qa_id": qa_id,
            "question": question.strip(),
            "question_hash": question_hash,
            "answer": answer,
            "answering_agent": answering_agent,
            "answering_model": answering_model,
            "created_at": timestamp,
            "aigise_session_id": aigise_session_id,
            "metadata": json.dumps(metadata or {}),
        }

    result = await client.run_query(query, params)

    if result:
        row = result[0]
        logger.info(f"Cache {row['action']} for question (hash={question_hash[:8]}...)")
        response = {
            "success": True,
            "qa_id": row["qa_id"],
            "action": row["action"],
            "message": f"Successfully {row['action']} cache entry",
            "embedding_stored": embedding is not None,
        }
        if embedding_error:
            response["embedding_error"] = embedding_error
        return response

    return {"success": False, "error": "No result returned from cache operation"}


@safe_tool_execution
async def create_cache_relation(
    source_match: Dict[str, Any],
    target_match: Dict[str, Any],
    relation_type: str,
    *,
    source_node_type: str = "QACache",
    target_node_type: str = "QACache",
    relation_properties: Optional[Dict[str, Any]] = None,
    database: str = "memory",
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Create a relationship between two nodes in Neo4j.

    This is a generic tool that can create any relationship type between any node
    types. Use this to connect documentation pages, Q&A pairs, or any other nodes.

    Args:
        source_match: Dictionary of property-value pairs to match the source node.
                     Example: {"question": "Overview"} or {"qa_id": "123"}
        target_match: Dictionary of property-value pairs to match the target node.
                     Example: {"question": "Architecture"} or {"qa_id": "456"}
        relation_type: Type of relationship to create (e.g., "RELATED_TO",
                      "DEPENDS_ON", "REFERENCES")
        source_node_type: Label/type of the source node. Default is "QACache".
        target_node_type: Label/type of the target node. Default is "QACache".
        relation_properties: Optional dictionary of properties to set on the
                           relationship. Example: {"created_at": "2024-01-01",
                           "weight": 0.8}
        database: Neo4j database type ("memory", "history", "analysis", etc.).
                 Default is "memory".

    Returns:
        Dictionary with:
        - success: True if relationship was created successfully
        - relation_type: The relationship type that was created
        - source: Information about the source node
        - target: Information about the target node
        - message: Success or error message
    """
    client = await get_neo4j_client_from_context(tool_context, database)
    timestamp = datetime.now().isoformat()

    # Build WHERE clause for source node matching
    source_where_parts = [f"source.{k} = ${f'source_{k}'}" for k in source_match.keys()]
    source_where = " AND ".join(source_where_parts)

    # Build WHERE clause for target node matching
    target_where_parts = [f"target.{k} = ${f'target_{k}'}" for k in target_match.keys()]
    target_where = " AND ".join(target_where_parts)

    # Build SET clause for relationship properties
    relation_set_parts = []
    params = {}

    # Add source match parameters
    for k, v in source_match.items():
        params[f"source_{k}"] = v

    # Add target match parameters
    for k, v in target_match.items():
        params[f"target_{k}"] = v

    # Add relationship properties (including default timestamp)
    if relation_properties:
        for k, v in relation_properties.items():
            relation_set_parts.append(f"r.{k} = ${f'rel_{k}'}")
            params[f"rel_{k}"] = v

    relation_set_parts.append("r.created_at = $timestamp")
    params["timestamp"] = timestamp

    relation_set = ", ".join(relation_set_parts) if relation_set_parts else ""

    # Build Cypher query
    query = f"""
    MATCH (source:{source_node_type})
    WHERE {source_where}
    MATCH (target:{target_node_type})
    WHERE {target_where}
    MERGE (source)-[r:{relation_type}]->(target)
    {f"SET {relation_set}" if relation_set else ""}
    RETURN source.question as source_question,
           source.qa_id as source_qa_id,
           target.question as target_question,
           target.qa_id as target_qa_id,
           type(r) as relation_type
    LIMIT 1
    """

    try:
        result = await client.run_query(query, params)

        if result and len(result) > 0:
            row = result[0]
            logger.info(
                f"Created {relation_type} relationship: "
                f"{row['source_question']} -> {row['target_question']}"
            )
            return {
                "success": True,
                "relation_type": relation_type,
                "source": {
                    "question": row.get("source_question"),
                    "qa_id": row.get("source_qa_id"),
                },
                "target": {
                    "question": row.get("target_question"),
                    "qa_id": row.get("target_qa_id"),
                },
                "message": f"Successfully created {relation_type} relationship",
            }
        else:
            return {
                "success": False,
                "error": (
                    "No matching nodes found. Check that source_match and "
                    "target_match correctly identify existing nodes."
                ),
            }
    except Exception as e:
        logger.error(f"Failed to create relationship: {e}")
        return {
            "success": False,
            "error": f"Failed to create relationship: {str(e)}",
        }


async def ensure_memory_indexes(tool_context: ToolContext) -> bool:
    """
    Ensure required indexes exist on QACache nodes.

    This function is called during Memory Agent initialization to ensure
    optimal query performance. Creates both regular indexes and vector index
    for similarity search.

    Args:
        tool_context: The tool context for accessing Neo4j.

    Returns:
        True if indexes were created/verified successfully, False otherwise.
    """
    client = await get_neo4j_client_from_context(tool_context, "memory")

    try:
        # Create regular indexes for exact match
        await client.run_query(
            "CREATE INDEX qa_question_hash IF NOT EXISTS FOR (q:QACache) ON (q.question_hash)"
        )
        await client.run_query(
            "CREATE INDEX qa_question IF NOT EXISTS FOR (q:QACache) ON (q.question)"
        )
        logger.info("Memory cache regular indexes ensured")

        # Create vector index for similarity search
        # Neo4j 5.11+ supports vector indexes
        try:
            await client.run_query(
                f"""
                CREATE VECTOR INDEX qa_embedding_index IF NOT EXISTS
                FOR (q:QACache)
                ON (q.embedding)
                OPTIONS {{
                    indexConfig: {{
                        `vector.dimensions`: {EMBEDDING_DIMENSION},
                        `vector.similarity_function`: 'cosine'
                    }}
                }}
                """
            )
            logger.info("Memory cache vector index ensured")
        except Exception as ve:
            logger.warning(
                f"Failed to create vector index (may require Neo4j 5.11+): {ve}"
            )

        return True
    except Exception as e:
        logger.warning(f"Failed to create indexes (may already exist): {e}")
        return False
