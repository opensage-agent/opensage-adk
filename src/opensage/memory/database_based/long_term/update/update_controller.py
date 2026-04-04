"""Update controller for orchestrating memory updates."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from opensage.memory.database_based.long_term.update.entity_extractor import (
    EntityExtractor,
    ExtractedEntity,
)
from opensage.memory.database_based.long_term.update.graph_operations import (
    GraphOperations,
    OperationResult,
    OperationType,
)
from opensage.memory.database_based.long_term.update.operation_decider import (
    LLMOperationDecider,
)
from opensage.memory.database_based.long_term.update.relationship_discoverer import (
    DiscoveredRelationship,
    RelationshipDiscoverer,
)

if TYPE_CHECKING:
    from opensage.memory.database_based.long_term.config.domain_config import (
        DomainConfig,
    )

logger = logging.getLogger(__name__)


@dataclass
class UpdateResult:
    """Result of a memory update operation."""

    success: bool = True
    entities_added: int = 0
    entities_updated: int = 0
    entities_deleted: int = 0
    entities_skipped: int = 0
    relationships_added: int = 0
    entity_results: List[OperationResult] = field(default_factory=list)
    relationship_results: List[OperationResult] = field(default_factory=list)
    extracted_entities: List[ExtractedEntity] = field(default_factory=list)
    discovered_relationships: List[DiscoveredRelationship] = field(default_factory=list)
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class MemoryUpdateController:
    """Controller for orchestrating memory update operations."""

    def __init__(
        self,
        domain_config: Optional["DomainConfig"] = None,
        use_llm_extraction: bool = True,
        generate_embeddings: bool = True,
        similarity_threshold: float = 0.7,
        use_llm_decision: bool = False,
    ):
        self.domain_config = domain_config
        self.use_llm_decision = use_llm_decision
        self.entity_extractor = EntityExtractor(
            domain_config=domain_config,
            use_llm_extraction=use_llm_extraction,
            generate_embeddings=generate_embeddings,
        )
        self.relationship_discoverer = RelationshipDiscoverer(
            domain_config=domain_config,
            similarity_threshold=similarity_threshold,
        )
        self.graph_operations = GraphOperations(domain_config=domain_config)
        self.operation_decider = LLMOperationDecider() if use_llm_decision else None
        self._indexes_ensured = False

    async def store_qa_pair(
        self,
        question: str,
        answer: str,
        answering_agent: str,
        answering_model: str,
        client: Any,
        opensage_session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> UpdateResult:
        await self._ensure_indexes(client)

        try:
            extraction_metadata = {
                "question": question,
                "answer": answer,
                "answering_agent": answering_agent,
                "answering_model": answering_model,
                **(metadata or {}),
            }

            extraction_result = await self.entity_extractor.extract(
                content=answer,
                content_type="qa_pair",
                metadata=extraction_metadata,
            )

            if not extraction_result.success:
                return UpdateResult(success=False, error=extraction_result.error)

            relationships = await self.relationship_discoverer.discover(
                entities=extraction_result.entities,
                client=client,
            )

            entity_results = await self.graph_operations.add_entities_batch(
                entities=extraction_result.entities,
                client=client,
                opensage_session_id=opensage_session_id,
            )

            rel_results = await self.graph_operations.add_relationships_batch(
                relationships=relationships,
                client=client,
            )

            entities_added = sum(
                1
                for r in entity_results
                if r.operation == OperationType.ADD and r.success
            )
            entities_updated = sum(
                1
                for r in entity_results
                if r.operation == OperationType.UPDATE and r.success
            )
            relationships_added = sum(
                1 for r in rel_results if r.operation == OperationType.ADD and r.success
            )

            return UpdateResult(
                success=True,
                entities_added=entities_added,
                entities_updated=entities_updated,
                relationships_added=relationships_added,
                entity_results=entity_results,
                relationship_results=rel_results,
                extracted_entities=extraction_result.entities,
                discovered_relationships=relationships,
                metadata={
                    "question_hash": extraction_result.metadata.get("question_hash"),
                    "answer_id": extraction_result.metadata.get("answer_id"),
                },
            )

        except Exception as e:
            logger.error(f"Failed to store Q&A pair: {e}")
            return UpdateResult(success=False, error=str(e))

    async def store_knowledge(
        self,
        content: str,
        content_type: str = "text",
        client: Any = None,
        opensage_session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> UpdateResult:
        if client is None:
            raise ValueError("Neo4j client is required")

        await self._ensure_indexes(client)

        try:
            extraction_result = await self.entity_extractor.extract(
                content=content,
                content_type=content_type,
                metadata=metadata,
            )

            if not extraction_result.success:
                return UpdateResult(success=False, error=extraction_result.error)

            relationships = await self.relationship_discoverer.discover(
                entities=extraction_result.entities,
                client=client,
            )

            entity_results = await self.graph_operations.add_entities_batch(
                entities=extraction_result.entities,
                client=client,
                opensage_session_id=opensage_session_id,
            )

            rel_results = await self.graph_operations.add_relationships_batch(
                relationships=relationships,
                client=client,
            )

            return UpdateResult(
                success=True,
                entities_added=sum(
                    1 for r in entity_results if r.operation == OperationType.ADD
                ),
                entities_updated=sum(
                    1 for r in entity_results if r.operation == OperationType.UPDATE
                ),
                relationships_added=sum(
                    1 for r in rel_results if r.operation == OperationType.ADD
                ),
                entity_results=entity_results,
                relationship_results=rel_results,
                extracted_entities=extraction_result.entities,
                discovered_relationships=relationships,
            )

        except Exception as e:
            logger.error(f"Failed to store knowledge: {e}")
            return UpdateResult(success=False, error=str(e))

    async def link_entities(
        self,
        source_label: str,
        source_key: Dict[str, Any],
        target_label: str,
        target_key: Dict[str, Any],
        relationship_type: str,
        client: Any,
        properties: Optional[Dict[str, Any]] = None,
    ) -> OperationResult:
        relationship = DiscoveredRelationship(
            type_name=relationship_type,
            source_label=source_label,
            source_key=source_key,
            target_label=target_label,
            target_key=target_key,
            properties=properties or {},
        )
        return await self.graph_operations.add_relationship(relationship, client)

    async def delete_entity(
        self,
        label: str,
        match_key: Dict[str, Any],
        client: Any,
    ) -> OperationResult:
        return await self.graph_operations.delete_entity(label, match_key, client)

    async def delete_relationship(
        self,
        rel_type: str,
        source_label: str,
        source_key: Dict[str, Any],
        target_label: str,
        target_key: Dict[str, Any],
        client: Any,
    ) -> OperationResult:
        return await self.graph_operations.delete_relationship(
            rel_type, source_label, source_key, target_label, target_key, client
        )

    async def store_knowledge_with_decision(
        self,
        content: str,
        content_type: str = "text",
        client: Any = None,
        opensage_session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> UpdateResult:
        if client is None:
            raise ValueError("Neo4j client is required")

        if self.operation_decider is None:
            return await self.store_knowledge(
                content, content_type, client, opensage_session_id, metadata
            )

        await self._ensure_indexes(client)

        try:
            extraction_result = await self.entity_extractor.extract(
                content=content,
                content_type=content_type,
                metadata=metadata,
            )

            if not extraction_result.success:
                return UpdateResult(success=False, error=extraction_result.error)

            entity_results = []
            entities_added = 0
            entities_updated = 0
            entities_deleted = 0
            entities_skipped = 0

            for entity in extraction_result.entities:
                existing_nodes = await self._find_similar_nodes(entity, client)
                operation = await self.operation_decider.decide_operation(
                    entity,
                    existing_nodes,
                    context={"content_type": content_type},
                )

                if operation == OperationType.ADD:
                    result = await self.graph_operations.add_entity(
                        entity, client, opensage_session_id
                    )
                    if result.success:
                        entities_added += 1
                    entity_results.append(result)
                elif operation == OperationType.UPDATE:
                    result = await self.graph_operations.add_entity(
                        entity, client, opensage_session_id
                    )
                    if result.success:
                        entities_updated += 1
                    entity_results.append(result)
                elif operation == OperationType.DELETE:
                    if existing_nodes:
                        match_key = self._get_match_key_from_node(
                            entity.label, existing_nodes[0]
                        )
                        result = await self.graph_operations.delete_entity(
                            entity.label, match_key, client
                        )
                        if result.success:
                            entities_deleted += 1
                        entity_results.append(result)
                else:
                    entities_skipped += 1
                    entity_results.append(
                        OperationResult(
                            operation=OperationType.NONE,
                            success=True,
                            label=entity.label,
                        )
                    )

            relationships = await self.relationship_discoverer.discover(
                entities=[
                    e
                    for e, r in zip(extraction_result.entities, entity_results)
                    if r.operation in (OperationType.ADD, OperationType.UPDATE)
                    and r.success
                ],
                client=client,
            )

            rel_results = await self.graph_operations.add_relationships_batch(
                relationships=relationships,
                client=client,
            )

            return UpdateResult(
                success=True,
                entities_added=entities_added,
                entities_updated=entities_updated,
                entities_deleted=entities_deleted,
                entities_skipped=entities_skipped,
                relationships_added=sum(
                    1 for r in rel_results if r.operation == OperationType.ADD
                ),
                entity_results=entity_results,
                relationship_results=rel_results,
                extracted_entities=extraction_result.entities,
                discovered_relationships=relationships,
            )

        except Exception as e:
            logger.error(f"Failed to store knowledge with decision: {e}")
            return UpdateResult(success=False, error=str(e))

    async def _find_similar_nodes(
        self,
        entity: ExtractedEntity,
        client: Any,
    ) -> List[Dict[str, Any]]:
        label = entity.label
        props = entity.properties

        if label == "Question":
            if "question_hash" in props:
                query = """
                MATCH (n:Question {question_hash: $hash})
                RETURN n.question_hash as question_hash, n.text as text,
                       'Question' as label, elementId(n) as id
                LIMIT 3
                """
                params = {"hash": props["question_hash"]}
            elif "embedding" in props:
                query = """
                CALL db.index.vector.queryNodes('question_embedding_index', 3, $embedding)
                YIELD node, score
                WHERE score > 0.8
                RETURN node.question_hash as question_hash, node.text as text,
                       'Question' as label, elementId(node) as id
                """
                params = {"embedding": props["embedding"]}
            else:
                return []
        elif label == "Topic":
            query = """
            MATCH (n:Topic)
            WHERE toLower(n.name) = toLower($name)
            RETURN n.name as name, 'Topic' as label, elementId(n) as id
            LIMIT 3
            """
            params = {"name": props.get("name", "")}
        elif label in ("Function", "Class"):
            query = f"""
            MATCH (n:{label})
            WHERE n.name = $name
            RETURN n.name as name, n.file_path as file_path,
                   '{label}' as label, elementId(n) as id
            LIMIT 3
            """
            params = {"name": props.get("name", "")}
        elif label == "File":
            query = """
            MATCH (n:File {path: $path})
            RETURN n.path as path, 'File' as label, elementId(n) as id
            LIMIT 1
            """
            params = {"path": props.get("path", "")}
        else:
            return []

        try:
            result = await client.run_query(query, params)
            return result if result else []
        except Exception as e:
            logger.warning(f"Failed to find similar nodes for {label}: {e}")
            return []

    def _get_match_key_from_node(
        self,
        label: str,
        node: Dict[str, Any],
    ) -> Dict[str, Any]:
        key_fields = {
            "Question": ["question_hash"],
            "Answer": ["answer_id"],
            "Topic": ["name"],
            "Function": ["name", "file_path"],
            "Class": ["name", "file_path"],
            "File": ["path"],
        }
        fields = key_fields.get(label, [])
        return {f: node[f] for f in fields if f in node}

    async def _ensure_indexes(self, client: Any) -> None:
        if not self._indexes_ensured:
            await self.graph_operations.ensure_indexes(client)
            self._indexes_ensured = True
