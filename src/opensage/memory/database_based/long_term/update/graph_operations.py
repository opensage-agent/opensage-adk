"""Graph operations for the memory system."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from opensage.memory.database_based.long_term.update.entity_extractor import (
    ExtractedEntity,
)
from opensage.memory.database_based.long_term.update.relationship_discoverer import (
    DiscoveredRelationship,
)

if TYPE_CHECKING:
    from opensage.memory.database_based.long_term.config.domain_config import (
        DomainConfig,
    )

logger = logging.getLogger(__name__)


class OperationType(Enum):
    """Types of graph operations."""

    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    NONE = "none"


@dataclass
class OperationResult:
    """Result of a graph operation."""

    operation: OperationType
    success: bool = True
    node_id: Optional[str] = None
    label: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


class GraphOperations:
    """Executes graph operations for adding, updating, and deleting nodes."""

    def __init__(
        self,
        domain_config: Optional["DomainConfig"] = None,
    ):
        self.domain_config = domain_config

    async def add_entity(
        self,
        entity: ExtractedEntity,
        client: Any,
        opensage_session_id: Optional[str] = None,
    ) -> OperationResult:
        label = entity.label
        props = dict(entity.properties)
        if opensage_session_id:
            props["opensage_session_id"] = opensage_session_id

        merge_key = self._get_merge_key(label, props)
        if not merge_key:
            return OperationResult(
                operation=OperationType.NONE,
                success=False,
                label=label,
                error=f"No merge key found for {label}",
            )

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
        results = []
        for rel in relationships:
            result = await self.add_relationship(rel, client)
            results.append(result)
        return results

    async def ensure_indexes(self, client: Any) -> bool:
        try:
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
        merge_parts = []
        params = {}
        for i, (key, value) in enumerate(merge_key.items()):
            param_name = f"mk_{i}"
            merge_parts.append(f"{key}: ${param_name}")
            params[param_name] = value

        merge_clause = f"(n:{label} {{{', '.join(merge_parts)}}})"

        on_create_parts = []
        on_match_parts = []
        for i, (key, value) in enumerate(props.items()):
            if key in merge_key:
                continue
            param_name = f"p_{i}"
            params[param_name] = self._serialize_property(value)

            if key in ("embedding", "created_at", "access_count"):
                on_create_parts.append(f"n.{key} = ${param_name}")
            elif key == "last_accessed":
                on_match_parts.append(f"n.{key} = ${param_name}")
                on_create_parts.append(f"n.{key} = ${param_name}")
            else:
                on_create_parts.append(f"n.{key} = ${param_name}")
                on_match_parts.append(f"n.{key} = ${param_name}")

        params["now"] = datetime.now().isoformat()
        on_create_parts.append("n.created_at = $now")
        on_match_parts.append("n.last_accessed = $now")

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
        params = {}
        source_where = []
        for i, (key, value) in enumerate(relationship.source_key.items()):
            param_name = f"sk_{i}"
            source_where.append(f"source.{key} = ${param_name}")
            params[param_name] = value

        target_where = []
        for i, (key, value) in enumerate(relationship.target_key.items()):
            param_name = f"tk_{i}"
            target_where.append(f"target.{key} = ${param_name}")
            params[param_name] = value

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
        if not match_key:
            return OperationResult(
                operation=OperationType.DELETE,
                success=False,
                label=label,
                error="No match key provided for deletion",
            )

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
        params = {}
        source_where = []
        for i, (key, value) in enumerate(source_key.items()):
            param_name = f"sk_{i}"
            source_where.append(f"source.{key} = ${param_name}")
            params[param_name] = value

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
        if isinstance(value, (dict, list)) and not isinstance(
            value[0] if isinstance(value, list) and value else None, (int, float)
        ):
            return json.dumps(value)
        return value
