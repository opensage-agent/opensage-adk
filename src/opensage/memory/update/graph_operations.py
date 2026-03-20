"""Graph operations for the memory system."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from opensage.memory.update.entity_extractor import ExtractedEntity
from opensage.memory.update.relationship_discoverer import DiscoveredRelationship

if TYPE_CHECKING:
    from opensage.memory.config.domain_config import DomainConfig

logger = logging.getLogger(__name__)


class OperationType(Enum):
    """Types of graph operations."""

    ADD = "add"
    """Add a new node or relationship."""

    UPDATE = "update"
    """Update an existing node or relationship."""

    DELETE = "delete"
    """Delete a node or relationship."""

    NONE = "none"
    """No operation (e.g., already exists)."""


@dataclass
class OperationResult:
    """Result of a graph operation."""

    operation: OperationType
    """Type of operation performed."""

    success: bool = True
    """Whether the operation succeeded."""

    node_id: Optional[str] = None
    """Neo4j element ID of affected node."""

    label: Optional[str] = None
    """Node label or relationship type."""

    properties: Dict[str, Any] = field(default_factory=dict)
    """Properties of the affected node/relationship."""

    error: Optional[str] = None
    """Error message if operation failed."""


class GraphOperations:
    """Executes graph operations for adding, updating, and deleting nodes/relationships.

    This class handles:
    - Creating new nodes with proper MERGE handling
    - Updating existing nodes
    - Creating relationships between nodes
    - Ensuring indexes exist
    """

    def __init__(
        self,
        domain_config: Optional["DomainConfig"] = None,
    ):
        """Initialize graph operations.

        Args:
            domain_config (Optional['DomainConfig']): Domain configuration for node/relationship schemas."""
        self.domain_config = domain_config

    async def add_entity(
        self,
        entity: ExtractedEntity,
        client: Any,
        opensage_session_id: Optional[str] = None,
    ) -> OperationResult:
        """Add an entity to the graph.

        Uses MERGE to avoid duplicates based on unique keys.

        Args:
            entity (ExtractedEntity): Entity to add.
            client (Any): Neo4j client.
            opensage_session_id (Optional[str]): Optional session ID for tracking.
        Returns:
            OperationResult: OperationResult with operation details.
        """
        label = entity.label
        props = dict(entity.properties)

        # Add session tracking if provided
        if opensage_session_id:
            props["opensage_session_id"] = opensage_session_id

        # Get merge key(s) based on entity type
        merge_key = self._get_merge_key(label, props)
        if not merge_key:
            return OperationResult(
                operation=OperationType.NONE,
                success=False,
                label=label,
                error=f"No merge key found for {label}",
            )

        # Build MERGE query
        query, params = self._build_merge_query(label, merge_key, props)

        try:
            result = await client.run_query(query, params)
            if result and len(result) > 0:
                row = result[0]
                return OperationResult(
                    operation=OperationType.ADD
                    if row.get("created", False)
                    else OperationType.UPDATE,
                    success=True,
                    node_id=row.get("node_id"),
                    label=label,
                    properties=row.get("props", {}),
                )
            return OperationResult(
                operation=OperationType.ADD,
                success=True,
                label=label,
            )
        except Exception as e:
            logger.error(f"Failed to add entity {label}: {e}")
            return OperationResult(
                operation=OperationType.ADD,
                success=False,
                label=label,
                error=str(e),
            )

    async def add_relationship(
        self,
        relationship: DiscoveredRelationship,
        client: Any,
    ) -> OperationResult:
        """Add a relationship to the graph.

        Args:
            relationship (DiscoveredRelationship): Relationship to add.
            client (Any): Neo4j client.
        Returns:
            OperationResult: OperationResult with operation details.
        """
        query, params = self._build_relationship_query(relationship)

        try:
            result = await client.run_query(query, params)
            if result and len(result) > 0:
                return OperationResult(
                    operation=OperationType.ADD,
                    success=True,
                    label=relationship.type_name,
                    properties=relationship.properties,
                )
            return OperationResult(
                operation=OperationType.NONE,
                success=True,
                label=relationship.type_name,
                error="Source or target node not found",
            )
        except Exception as e:
            logger.error(f"Failed to add relationship {relationship.type_name}: {e}")
            return OperationResult(
                operation=OperationType.ADD,
                success=False,
                label=relationship.type_name,
                error=str(e),
            )

    async def add_entities_batch(
        self,
        entities: List[ExtractedEntity],
        client: Any,
        opensage_session_id: Optional[str] = None,
    ) -> List[OperationResult]:
        """Add multiple entities to the graph.

        Args:
            entities (List[ExtractedEntity]): Entities to add.
            client (Any): Neo4j client.
            opensage_session_id (Optional[str]): Optional session ID.
        Returns:
            List[OperationResult]: List of operation results.
        """
        results = []
        for entity in entities:
            result = await self.add_entity(entity, client, opensage_session_id)
            results.append(result)
        return results

    async def add_relationships_batch(
        self,
        relationships: List[DiscoveredRelationship],
        client: Any,
    ) -> List[OperationResult]:
        """Add multiple relationships to the graph.

        Args:
            relationships (List[DiscoveredRelationship]): Relationships to add.
            client (Any): Neo4j client.
        Returns:
            List[OperationResult]: List of operation results.
        """
        results = []
        for rel in relationships:
            result = await self.add_relationship(rel, client)
            results.append(result)
        return results

    async def ensure_indexes(self, client: Any) -> bool:
        """Ensure required indexes exist.

        Creates both regular and vector indexes for memory nodes.

        Args:
            client (Any): Neo4j client.
        Returns:
            bool: True if indexes were created/verified successfully.
        """
        try:
            # Regular indexes for exact match
            await client.run_query(
                "CREATE INDEX question_hash_idx IF NOT EXISTS FOR (q:Question) ON (q.question_hash)"
            )
            await client.run_query(
                "CREATE INDEX answer_id_idx IF NOT EXISTS FOR (a:Answer) ON (a.answer_id)"
            )
            await client.run_query(
                "CREATE INDEX topic_name_idx IF NOT EXISTS FOR (t:Topic) ON (t.name)"
            )
            await client.run_query(
                "CREATE INDEX function_name_idx IF NOT EXISTS FOR (f:Function) ON (f.name)"
            )
            await client.run_query(
                "CREATE INDEX class_name_idx IF NOT EXISTS FOR (c:Class) ON (c.name)"
            )
            await client.run_query(
                "CREATE INDEX file_path_idx IF NOT EXISTS FOR (f:File) ON (f.path)"
            )
            await client.run_query(
                "CREATE INDEX text_hash_idx IF NOT EXISTS FOR (t:Text) ON (t.text_hash)"
            )
            logger.info("Regular indexes ensured")

            # Vector indexes for similarity search
            try:
                await client.run_query(
                    """
                    CREATE VECTOR INDEX question_embedding_index IF NOT EXISTS
                    FOR (q:Question)
                    ON (q.embedding)
                    OPTIONS {
                        indexConfig: {
                            `vector.dimensions`: 3072,
                            `vector.similarity_function`: 'cosine'
                        }
                    }
                    """
                )
                await client.run_query(
                    """
                    CREATE VECTOR INDEX text_embedding_index IF NOT EXISTS
                    FOR (t:Text)
                    ON (t.embedding)
                    OPTIONS {
                        indexConfig: {
                            `vector.dimensions`: 3072,
                            `vector.similarity_function`: 'cosine'
                        }
                    }
                    """
                )
                logger.info("Vector indexes ensured")
            except Exception as ve:
                logger.warning(
                    f"Failed to create vector indexes (may require Neo4j 5.11+): {ve}"
                )

            # Full-text indexes for keyword search
            try:
                await client.run_query(
                    """
                    CREATE FULLTEXT INDEX question_fulltext_index IF NOT EXISTS
                    FOR (q:Question)
                    ON EACH [q.text]
                    """
                )
                await client.run_query(
                    """
                    CREATE FULLTEXT INDEX text_fulltext_index IF NOT EXISTS
                    FOR (t:Text)
                    ON EACH [t.text]
                    """
                )
                await client.run_query(
                    """
                    CREATE FULLTEXT INDEX answer_fulltext_index IF NOT EXISTS
                    FOR (a:Answer)
                    ON EACH [a.text]
                    """
                )
                await client.run_query(
                    """
                    CREATE FULLTEXT INDEX topic_fulltext_index IF NOT EXISTS
                    FOR (t:Topic)
                    ON EACH [t.name]
                    """
                )
                await client.run_query(
                    """
                    CREATE FULLTEXT INDEX function_fulltext_index IF NOT EXISTS
                    FOR (f:Function)
                    ON EACH [f.name]
                    """
                )
                await client.run_query(
                    """
                    CREATE FULLTEXT INDEX class_fulltext_index IF NOT EXISTS
                    FOR (c:Class)
                    ON EACH [c.name]
                    """
                )
                logger.info("Full-text indexes ensured")
            except Exception as fe:
                logger.warning(f"Failed to create full-text indexes: {fe}")

            return True
        except Exception as e:
            logger.error(f"Failed to create indexes: {e}")
            return False

    def _get_merge_key(self, label: str, props: Dict[str, Any]) -> Dict[str, Any]:
        """Get the merge key properties for a node type."""
        merge_keys = {
            "Question": ["question_hash"],
            "Answer": ["answer_id"],
            "Topic": ["name"],
            "Text": ["text_hash"],
            "Function": ["name", "file_path"],
            "Class": ["name", "file_path"],
            "File": ["path"],
        }

        key_props = merge_keys.get(label, [])
        if not key_props:
            return {}

        return {k: props[k] for k in key_props if k in props}

    def _build_merge_query(
        self, label: str, merge_key: Dict[str, Any], props: Dict[str, Any]
    ) -> tuple:
        """Build a MERGE query with ON CREATE and ON MATCH clauses."""
        # Build merge key part
        merge_parts = []
        params = {}
        for i, (key, value) in enumerate(merge_key.items()):
            param_name = f"mk_{i}"
            merge_parts.append(f"{key}: ${param_name}")
            params[param_name] = value

        merge_clause = f"(n:{label} {{{', '.join(merge_parts)}}})"

        # Build property sets
        on_create_parts = []
        on_match_parts = []
        for i, (key, value) in enumerate(props.items()):
            if key in merge_key:
                continue  # Skip merge key properties

            param_name = f"p_{i}"
            params[param_name] = self._serialize_property(value)

            if key in ("embedding", "created_at", "access_count"):
                # Only set on create
                on_create_parts.append(f"n.{key} = ${param_name}")
            elif key == "last_accessed":
                # Always update
                on_match_parts.append(f"n.{key} = ${param_name}")
                on_create_parts.append(f"n.{key} = ${param_name}")
            else:
                # Set on both
                on_create_parts.append(f"n.{key} = ${param_name}")
                on_match_parts.append(f"n.{key} = ${param_name}")

        # Add timestamp
        params["now"] = datetime.now().isoformat()
        on_create_parts.append("n.created_at = $now")
        on_match_parts.append("n.last_accessed = $now")

        # Build query
        query = f"""
        MERGE {merge_clause}
        ON CREATE SET {", ".join(on_create_parts) if on_create_parts else "n.created_at = $now"}
        ON MATCH SET {", ".join(on_match_parts) if on_match_parts else "n.last_accessed = $now"}
        RETURN elementId(n) as node_id,
               properties(n) as props,
               n.created_at = $now as created
        """

        return query, params

    def _build_relationship_query(self, relationship: DiscoveredRelationship) -> tuple:
        """Build a relationship creation query."""
        params = {}

        # Source match conditions
        source_where = []
        for i, (key, value) in enumerate(relationship.source_key.items()):
            param_name = f"sk_{i}"
            source_where.append(f"source.{key} = ${param_name}")
            params[param_name] = value

        # Target match conditions
        target_where = []
        for i, (key, value) in enumerate(relationship.target_key.items()):
            param_name = f"tk_{i}"
            target_where.append(f"target.{key} = ${param_name}")
            params[param_name] = value

        # Relationship properties
        rel_props = dict(relationship.properties)
        rel_props["created_at"] = datetime.now().isoformat()

        rel_set_parts = []
        for i, (key, value) in enumerate(rel_props.items()):
            param_name = f"rp_{i}"
            rel_set_parts.append(f"r.{key} = ${param_name}")
            params[param_name] = self._serialize_property(value)

        query = f"""
        MATCH (source:{relationship.source_label})
        WHERE {" AND ".join(source_where)}
        MATCH (target:{relationship.target_label})
        WHERE {" AND ".join(target_where)}
        MERGE (source)-[r:{relationship.type_name}]->(target)
        SET {", ".join(rel_set_parts)}
        RETURN elementId(r) as rel_id, type(r) as rel_type
        """

        return query, params

    async def delete_entity(
        self,
        label: str,
        match_key: Dict[str, Any],
        client: Any,
    ) -> OperationResult:
        """Delete a node from the graph.

        Uses DETACH DELETE to remove the node and all its relationships.

        Args:
            label (str): Node label (e.g., "Question", "Topic").
            match_key (Dict[str, Any]): Properties to identify the node to delete.
            client (Any): Neo4j client.
        Returns:
            OperationResult: OperationResult with operation details.
        """
        if not match_key:
            return OperationResult(
                operation=OperationType.DELETE,
                success=False,
                label=label,
                error="No match key provided for deletion",
            )

        # Build WHERE clause
        where_parts = []
        params = {}
        for i, (key, value) in enumerate(match_key.items()):
            param_name = f"k{i}"
            where_parts.append(f"n.{key} = ${param_name}")
            params[param_name] = value

        query = f"""
        MATCH (n:{label})
        WHERE {" AND ".join(where_parts)}
        DETACH DELETE n
        RETURN count(n) as deleted
        """

        try:
            result = await client.run_query(query, params)
            deleted = result[0]["deleted"] if result else 0

            return OperationResult(
                operation=OperationType.DELETE,
                success=deleted > 0,
                label=label,
                properties=match_key,
                error=None if deleted > 0 else "No matching node found",
            )
        except Exception as e:
            logger.error(f"Failed to delete entity {label}: {e}")
            return OperationResult(
                operation=OperationType.DELETE,
                success=False,
                label=label,
                error=str(e),
            )

    async def delete_relationship(
        self,
        rel_type: str,
        source_label: str,
        source_key: Dict[str, Any],
        target_label: str,
        target_key: Dict[str, Any],
        client: Any,
    ) -> OperationResult:
        """Delete a relationship from the graph.

        Args:
            rel_type (str): Type of relationship to delete.
            source_label (str): Label of source node.
            source_key (Dict[str, Any]): Properties to identify source node.
            target_label (str): Label of target node.
            target_key (Dict[str, Any]): Properties to identify target node.
            client (Any): Neo4j client.
        Returns:
            OperationResult: OperationResult with operation details.
        """
        params = {}

        # Source match conditions
        source_where = []
        for i, (key, value) in enumerate(source_key.items()):
            param_name = f"sk_{i}"
            source_where.append(f"source.{key} = ${param_name}")
            params[param_name] = value

        # Target match conditions
        target_where = []
        for i, (key, value) in enumerate(target_key.items()):
            param_name = f"tk_{i}"
            target_where.append(f"target.{key} = ${param_name}")
            params[param_name] = value

        query = f"""
        MATCH (source:{source_label})-[r:{rel_type}]->(target:{target_label})
        WHERE {" AND ".join(source_where)} AND {" AND ".join(target_where)}
        DELETE r
        RETURN count(r) as deleted
        """

        try:
            result = await client.run_query(query, params)
            deleted = result[0]["deleted"] if result else 0

            return OperationResult(
                operation=OperationType.DELETE,
                success=deleted > 0,
                label=rel_type,
                error=None if deleted > 0 else "No matching relationship found",
            )
        except Exception as e:
            logger.error(f"Failed to delete relationship {rel_type}: {e}")
            return OperationResult(
                operation=OperationType.DELETE,
                success=False,
                label=rel_type,
                error=str(e),
            )

    def _serialize_property(self, value: Any) -> Any:
        """Serialize a property value for Neo4j."""
        if isinstance(value, (dict, list)) and not isinstance(
            value[0] if isinstance(value, list) and value else None, (int, float)
        ):
            # JSON serialize complex objects (but not embedding arrays)
            return json.dumps(value)
        return value
