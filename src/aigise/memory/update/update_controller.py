"""Update controller for orchestrating memory updates."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from opensage.memory.update.entity_extractor import (
    EntityExtractor,
    ExtractedEntity,
    ExtractionResult,
)
from opensage.memory.update.graph_operations import (
    GraphOperations,
    OperationResult,
    OperationType,
)
from opensage.memory.update.operation_decider import LLMOperationDecider
from opensage.memory.update.relationship_discoverer import (
    DiscoveredRelationship,
    RelationshipDiscoverer,
)

if TYPE_CHECKING:
    from opensage.memory.config.domain_config import DomainConfig

logger = logging.getLogger(__name__)


@dataclass
class UpdateResult:
    """Result of a memory update operation."""

    success: bool = True
    """Whether the update succeeded overall."""

    entities_added: int = 0
    """Number of entities added."""

    entities_updated: int = 0
    """Number of entities updated."""

    entities_deleted: int = 0
    """Number of entities deleted."""

    entities_skipped: int = 0
    """Number of entities skipped (already exist, no changes needed)."""

    relationships_added: int = 0
    """Number of relationships created."""

    entity_results: List[OperationResult] = field(default_factory=list)
    """Detailed results for entity operations."""

    relationship_results: List[OperationResult] = field(default_factory=list)
    """Detailed results for relationship operations."""

    extracted_entities: List[ExtractedEntity] = field(default_factory=list)
    """Entities that were extracted."""

    discovered_relationships: List[DiscoveredRelationship] = field(default_factory=list)
    """Relationships that were discovered."""

    error: Optional[str] = None
    """Error message if update failed."""

    metadata: Dict[str, Any] = field(default_factory=dict)
    """Additional update metadata."""


class MemoryUpdateController:
    """Controller for orchestrating memory update operations.

    This controller:
    1. Extracts entities from content (using EntityExtractor)
    2. Discovers relationships between entities (using RelationshipDiscoverer)
    3. Decides operation type for each entity (using LLMOperationDecider)
    4. Executes graph operations (using GraphOperations)
    """

    def __init__(
        self,
        domain_config: Optional["DomainConfig"] = None,
        use_llm_extraction: bool = True,
        generate_embeddings: bool = True,
        similarity_threshold: float = 0.7,
        use_llm_decision: bool = False,
    ):
        """Initialize the update controller.

        Args:
            domain_config (Optional['DomainConfig']): Domain configuration defining entity types.
            use_llm_extraction (bool): Whether to use LLM for semantic extraction.
            generate_embeddings (bool): Whether to generate embeddings for entities.
            similarity_threshold (float): Threshold for similarity-based relationships.
            use_llm_decision (bool): Whether to use LLM for operation type decisions."""
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
        """Store a question-answer pair in the memory graph.

        This is the main entry point for storing Q&A knowledge.
        It extracts entities, discovers relationships, and persists to Neo4j.

        Args:
            question (str): The question text.
            answer (str): The answer text.
            answering_agent (str): Name of the agent that generated the answer.
            answering_model (str): Model used to generate the answer.
            client (Any): Neo4j client.
            opensage_session_id (Optional[str]): Optional session ID for tracking.
            metadata (Optional[Dict[str, Any]]): Additional metadata to store.
        Returns:
            UpdateResult: UpdateResult with operation details.
        """
        # Ensure indexes exist
        await self._ensure_indexes(client)

        try:
            # 1. Extract entities
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
                return UpdateResult(
                    success=False,
                    error=extraction_result.error,
                )

            # 2. Discover relationships
            relationships = await self.relationship_discoverer.discover(
                entities=extraction_result.entities,
                client=client,
            )

            # 3. Add entities to graph
            entity_results = await self.graph_operations.add_entities_batch(
                entities=extraction_result.entities,
                client=client,
                opensage_session_id=opensage_session_id,
            )

            # 4. Add relationships to graph
            rel_results = await self.graph_operations.add_relationships_batch(
                relationships=relationships,
                client=client,
            )

            # Count operations
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
            return UpdateResult(
                success=False,
                error=str(e),
            )

    async def store_knowledge(
        self,
        content: str,
        content_type: str = "text",
        client: Any = None,
        opensage_session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> UpdateResult:
        """Store knowledge in the memory graph.

                Generic method for storing any type of content.

                Args:
                    content (str): Content to store.
                    content_type (str): Type of content ('text', 'code', 'question', 'answer').
                    client (Any): Neo4j client.
                    opensage_session_id (Optional[str]): Optional session ID.
                    metadata (Optional[Dict[str, Any]]): Additional metadata.

        Raises:
          ValueError: Raised when this operation fails.
                Returns:
                    UpdateResult: UpdateResult with operation details.
        """
        if client is None:
            raise ValueError("Neo4j client is required")

        await self._ensure_indexes(client)

        try:
            # Extract entities
            extraction_result = await self.entity_extractor.extract(
                content=content,
                content_type=content_type,
                metadata=metadata,
            )

            if not extraction_result.success:
                return UpdateResult(
                    success=False,
                    error=extraction_result.error,
                )

            # Discover relationships
            relationships = await self.relationship_discoverer.discover(
                entities=extraction_result.entities,
                client=client,
            )

            # Add entities
            entity_results = await self.graph_operations.add_entities_batch(
                entities=extraction_result.entities,
                client=client,
                opensage_session_id=opensage_session_id,
            )

            # Add relationships
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
        """Create a relationship between two existing entities.

        Args:
            source_label (str): Label of source node.
            source_key (Dict[str, Any]): Properties to identify source node.
            target_label (str): Label of target node.
            target_key (Dict[str, Any]): Properties to identify target node.
            relationship_type (str): Type of relationship to create.
            client (Any): Neo4j client.
            properties (Optional[Dict[str, Any]]): Optional relationship properties.
        Returns:
            OperationResult: OperationResult for the relationship creation.
        """
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
        """Delete an entity from the memory graph.

        Args:
            label (str): Node label (e.g., "Question", "Topic").
            match_key (Dict[str, Any]): Properties to identify the node.
            client (Any): Neo4j client.
        Returns:
            OperationResult: OperationResult with operation details.
        """
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
        """Delete a relationship from the memory graph.

        Args:
            rel_type (str): Relationship type.
            source_label (str): Label of source node.
            source_key (Dict[str, Any]): Properties to identify source node.
            target_label (str): Label of target node.
            target_key (Dict[str, Any]): Properties to identify target node.
            client (Any): Neo4j client.
        Returns:
            OperationResult: OperationResult with operation details.
        """
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
        """Store knowledge using LLM to decide operation type for each entity.

                This method uses the LLMOperationDecider to intelligently decide whether
                to ADD, UPDATE, DELETE, or skip each extracted entity based on what
                already exists in the graph.

                Args:
                    content (str): Content to store.
                    content_type (str): Type of content.
                    client (Any): Neo4j client.
                    opensage_session_id (Optional[str]): Optional session ID.
                    metadata (Optional[Dict[str, Any]]): Additional metadata.

        Raises:
          ValueError: Raised when this operation fails.
                Returns:
                    UpdateResult: UpdateResult with operation details.
        """
        if client is None:
            raise ValueError("Neo4j client is required")

        if self.operation_decider is None:
            # Fall back to regular store_knowledge if no decider configured
            return await self.store_knowledge(
                content, content_type, client, opensage_session_id, metadata
            )

        await self._ensure_indexes(client)

        try:
            # Extract entities
            extraction_result = await self.entity_extractor.extract(
                content=content,
                content_type=content_type,
                metadata=metadata,
            )

            if not extraction_result.success:
                return UpdateResult(
                    success=False,
                    error=extraction_result.error,
                )

            # Process each entity with LLM decision
            entity_results = []
            entities_added = 0
            entities_updated = 0
            entities_deleted = 0
            entities_skipped = 0

            for entity in extraction_result.entities:
                # Find similar existing nodes
                existing_nodes = await self._find_similar_nodes(entity, client)

                # Let LLM decide the operation
                operation = await self.operation_decider.decide_operation(
                    entity,
                    existing_nodes,
                    context={"content_type": content_type},
                )

                # Execute based on decision
                if operation == OperationType.ADD:
                    result = await self.graph_operations.add_entity(
                        entity, client, opensage_session_id
                    )
                    if result.success:
                        entities_added += 1
                    entity_results.append(result)

                elif operation == OperationType.UPDATE:
                    # Update is handled by add_entity with MERGE
                    result = await self.graph_operations.add_entity(
                        entity, client, opensage_session_id
                    )
                    if result.success:
                        entities_updated += 1
                    entity_results.append(result)

                elif operation == OperationType.DELETE:
                    if existing_nodes:
                        # Delete the first matching node
                        match_key = self._get_match_key_from_node(
                            entity.label, existing_nodes[0]
                        )
                        result = await self.graph_operations.delete_entity(
                            entity.label, match_key, client
                        )
                        if result.success:
                            entities_deleted += 1
                        entity_results.append(result)

                else:  # NONE
                    entities_skipped += 1
                    entity_results.append(
                        OperationResult(
                            operation=OperationType.NONE,
                            success=True,
                            label=entity.label,
                        )
                    )

            # Discover and add relationships for added/updated entities
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
        """Find existing nodes similar to the given entity.

        Args:
            entity (ExtractedEntity): The entity to find similar nodes for.
            client (Any): Neo4j client.
        Returns:
            List[Dict[str, Any]]: List of similar node dictionaries.
        """
        label = entity.label
        props = entity.properties

        # Build query based on entity type
        if label == "Question":
            # Exact match on hash or similarity on embedding
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
            # Match on name
            query = """
            MATCH (n:Topic)
            WHERE toLower(n.name) = toLower($name)
            RETURN n.name as name, 'Topic' as label, elementId(n) as id
            LIMIT 3
            """
            params = {"name": props.get("name", "")}

        elif label in ("Function", "Class"):
            # Match on name and file_path
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
        """Extract match key properties from a node dictionary.

        Args:
            label (str): Node label.
            node (Dict[str, Any]): Node properties dictionary.
        Returns:
            Dict[str, Any]: Dictionary of match key properties.
        """
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
        """Ensure indexes exist (called once per controller instance)."""
        if not self._indexes_ensured:
            await self.graph_operations.ensure_indexes(client)
            self._indexes_ensured = True
