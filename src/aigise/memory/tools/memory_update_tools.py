"""Memory update tools for agents."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from google.adk.tools.tool_context import ToolContext

from aigise.memory.config import get_merged_domain
from aigise.memory.update.graph_operations import GraphOperations
from aigise.memory.update.update_controller import MemoryUpdateController
from aigise.toolbox.decorators import requires_sandbox, safe_tool_execution
from aigise.utils.agent_utils import (
    get_aigise_session_id_from_context,
    get_neo4j_client_from_context,
)

logger = logging.getLogger(__name__)

# Singleton update controller
_update_controller: Optional[MemoryUpdateController] = None


def _get_update_controller() -> MemoryUpdateController:
    """Get or create the update controller singleton."""
    global _update_controller
    if _update_controller is None:
        try:
            domain = get_merged_domain("code", "qa")
        except Exception:
            domain = None
        _update_controller = MemoryUpdateController(
            domain_config=domain,
            use_llm_extraction=True,
            generate_embeddings=True,
        )
    return _update_controller


@safe_tool_execution
@requires_sandbox("neo4j")
async def store_knowledge(
    content: str,
    content_type: str = "text",
    *,
    metadata: Optional[Dict[str, Any]] = None,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """
    Store knowledge in the memory graph.

    Use this tool to persist any kind of knowledge for future retrieval.
    The system will automatically extract entities, topics, and
    relationships from the content.

    Args:
        content: The content to store.
        content_type: Type of content. Options:
                     - "text": Generic text content
                     - "code": Code content (will extract functions/classes)
                     - "question": A question to cache
                     - "answer": An answer to cache
        metadata: Optional additional metadata to store.

    Returns:
        Dictionary with:
        - success: True if storage succeeded
        - entities_added: Number of entities created
        - entities_updated: Number of existing entities updated
        - relationships_added: Number of relationships created
        - message: Summary of what was stored
    """
    client = await get_neo4j_client_from_context(tool_context, "memory")
    aigise_session_id = get_aigise_session_id_from_context(tool_context)
    controller = _get_update_controller()

    result = await controller.store_knowledge(
        content=content,
        content_type=content_type,
        client=client,
        aigise_session_id=aigise_session_id,
        metadata=metadata,
    )

    response = {
        "success": result.success,
        "entities_added": result.entities_added,
        "entities_updated": result.entities_updated,
        "relationships_added": result.relationships_added,
    }

    if result.success:
        entities_summary = []
        for entity in result.extracted_entities[:5]:
            entities_summary.append(
                f"{entity.label}: {entity.properties.get('name', entity.properties.get('text', '')[:50])}"
            )

        response["message"] = (
            f"Stored {result.entities_added} new entities, "
            f"updated {result.entities_updated} existing, "
            f"created {result.relationships_added} relationships"
        )
        response["entities_stored"] = entities_summary
    else:
        response["error"] = result.error

    logger.info(
        f"Knowledge storage: {response['message'] if result.success else result.error}"
    )
    return response


@safe_tool_execution
@requires_sandbox("neo4j")
async def cache_qa_pair(
    question: str,
    answer: str,
    answering_agent: str,
    answering_model: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """
    Cache a question-answer pair in the memory graph.

    Use this tool AFTER successfully answering a question to store
    it for future retrieval. This enables the agent to quickly
    answer similar questions in the future.

    The system automatically:
    - Generates embeddings for similarity search
    - Extracts topics and code references
    - Creates relationships to related entities
    - Finds connections to existing cached questions

    Args:
        question: The question text that was asked.
        answer: The answer text that was generated.
        answering_agent: Name of the agent that generated the answer.
        answering_model: Model identifier used to generate the answer.
        metadata: Optional additional metadata to store.

    Returns:
        Dictionary with:
        - success: True if caching succeeded
        - question_hash: Hash of the question for lookup
        - answer_id: Unique ID of the stored answer
        - entities_added: Number of entities created
        - relationships_added: Number of relationships created
        - topics_identified: Topics extracted from the Q&A
        - related_questions: Number of similar questions found
    """
    client = await get_neo4j_client_from_context(tool_context, "memory")
    aigise_session_id = get_aigise_session_id_from_context(tool_context)
    controller = _get_update_controller()

    result = await controller.store_qa_pair(
        question=question,
        answer=answer,
        answering_agent=answering_agent,
        answering_model=answering_model,
        client=client,
        aigise_session_id=aigise_session_id,
        metadata=metadata,
    )

    response = {
        "success": result.success,
        "question_hash": result.metadata.get("question_hash"),
        "answer_id": result.metadata.get("answer_id"),
        "entities_added": result.entities_added,
        "entities_updated": result.entities_updated,
        "relationships_added": result.relationships_added,
    }

    if result.success:
        # Extract topics from entities
        topics = [
            e.properties.get("name")
            for e in result.extracted_entities
            if e.label == "Topic" and e.properties.get("name")
        ]
        response["topics_identified"] = topics[:5]

        # Count related questions from relationships
        related_count = sum(
            1 for r in result.discovered_relationships if r.type_name == "RELATED_TO"
        )
        response["related_questions"] = related_count

        response["message"] = (
            f"Cached Q&A with {len(topics)} topics and "
            f"{related_count} related questions found"
        )
    else:
        response["error"] = result.error

    logger.info(
        f"Q&A caching: {'success' if result.success else 'failed'} "
        f"for question: {question[:50]}..."
    )
    return response


@safe_tool_execution
@requires_sandbox("neo4j")
async def link_entities(
    source_label: str,
    source_key: Dict[str, Any],
    target_label: str,
    target_key: Dict[str, Any],
    relationship_type: str,
    *,
    properties: Optional[Dict[str, Any]] = None,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """
    Create a relationship between two entities in the memory graph.

    Use this to manually connect related knowledge that the automatic
    extraction might have missed.

    Args:
        source_label: Label of the source node (e.g., "Question").
        source_key: Properties to identify source (e.g., {"question_hash": "..."}).
        target_label: Label of the target node (e.g., "Topic").
        target_key: Properties to identify target (e.g., {"name": "..."}).
        relationship_type: Type of relationship. Options:
                          - "ABOUT": What something is about
                          - "HAS_ANSWER": Question to answer link
                          - "HAS_TOPIC": Content to topic link
                          - "RELATED_TO": General relation
                          - "MENTIONS": Content mentions code entity
        properties: Optional properties for the relationship.

    Returns:
        Dictionary with:
        - success: True if relationship was created
        - relationship_type: The type of relationship created
        - message: Description of what was linked
    """
    client = await get_neo4j_client_from_context(tool_context, "memory")
    controller = _get_update_controller()

    result = await controller.link_entities(
        source_label=source_label,
        source_key=source_key,
        target_label=target_label,
        target_key=target_key,
        relationship_type=relationship_type,
        client=client,
        properties=properties,
    )

    response = {
        "success": result.success,
        "relationship_type": relationship_type,
    }

    if result.success:
        response["message"] = (
            f"Created {relationship_type} relationship from "
            f"{source_label} to {target_label}"
        )
    else:
        response["error"] = result.error

    return response


@safe_tool_execution
@requires_sandbox("neo4j")
async def delete_from_memory(
    node_label: str,
    node_key: Dict[str, Any],
    *,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """
    Delete an entity from the memory graph.

    Use this to remove outdated or incorrect cached knowledge.
    This will also remove all relationships connected to the node.

    Args:
        node_label: Label of the node to delete. Options:
                   - "Question": A cached question
                   - "Answer": A cached answer
                   - "Topic": A topic/concept
                   - "Function": A function entity
                   - "Class": A class entity
                   - "File": A file entity
        node_key: Properties to identify the node. Examples:
                 - {"question_hash": "..."} for Question
                 - {"name": "..."} for Topic
                 - {"name": "...", "file_path": "..."} for Function/Class
                 - {"path": "..."} for File

    Returns:
        Dictionary with:
        - success: True if entity was deleted
        - message: Description of what was deleted
        - error: Error message if deletion failed
    """
    client = await get_neo4j_client_from_context(tool_context, "memory")
    controller = _get_update_controller()

    result = await controller.delete_entity(
        label=node_label,
        match_key=node_key,
        client=client,
    )

    response = {
        "success": result.success,
        "node_label": node_label,
    }

    if result.success:
        response["message"] = f"Successfully deleted {node_label} node"
        logger.info(f"Deleted {node_label} with key {node_key}")
    else:
        response["error"] = result.error or "Unknown error"
        logger.warning(f"Failed to delete {node_label}: {result.error}")

    return response


@safe_tool_execution
@requires_sandbox("neo4j")
async def delete_relationship_from_memory(
    relationship_type: str,
    source_label: str,
    source_key: Dict[str, Any],
    target_label: str,
    target_key: Dict[str, Any],
    *,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """
    Delete a relationship from the memory graph.

    Use this to remove incorrect or outdated connections between entities.

    Args:
        relationship_type: Type of relationship to delete. Options:
                          - "ABOUT": What something is about
                          - "HAS_ANSWER": Question to answer link
                          - "HAS_TOPIC": Content to topic link
                          - "RELATED_TO": General relation
                          - "MENTIONS": Content mentions code entity
        source_label: Label of the source node.
        source_key: Properties to identify the source node.
        target_label: Label of the target node.
        target_key: Properties to identify the target node.

    Returns:
        Dictionary with:
        - success: True if relationship was deleted
        - message: Description of what was deleted
    """
    client = await get_neo4j_client_from_context(tool_context, "memory")
    controller = _get_update_controller()

    result = await controller.delete_relationship(
        rel_type=relationship_type,
        source_label=source_label,
        source_key=source_key,
        target_label=target_label,
        target_key=target_key,
        client=client,
    )

    response = {
        "success": result.success,
        "relationship_type": relationship_type,
    }

    if result.success:
        response["message"] = (
            f"Successfully deleted {relationship_type} relationship "
            f"from {source_label} to {target_label}"
        )
    else:
        response["error"] = result.error or "Unknown error"

    return response


@safe_tool_execution
@requires_sandbox("neo4j")
async def ensure_memory_indexes(
    *,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """
    Ensure memory graph indexes are created.

    This tool creates the necessary indexes for efficient querying:
    - Regular indexes for exact match lookups
    - Vector indexes for similarity search

    You typically don't need to call this manually as it's called
    automatically during the first cache operation.

    Returns:
        Dictionary with:
        - success: True if indexes were created/verified
        - message: Description of indexes created
    """
    client = await get_neo4j_client_from_context(tool_context, "memory")
    ops = GraphOperations()

    success = await ops.ensure_indexes(client)

    if success:
        return {
            "success": True,
            "message": "Memory indexes created/verified successfully",
            "indexes_created": [
                "question_hash_idx",
                "answer_id_idx",
                "topic_name_idx",
                "function_name_idx",
                "class_name_idx",
                "file_path_idx",
                "question_embedding_index (vector)",
                "topic_embedding_index (vector)",
            ],
        }
    else:
        return {
            "success": False,
            "error": "Failed to create some indexes - check logs for details",
        }
