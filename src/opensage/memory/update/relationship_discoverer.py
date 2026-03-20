"""Relationship discovery for the memory system."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from opensage.memory.update.entity_extractor import ExtractedEntity

if TYPE_CHECKING:
    from opensage.memory.config.domain_config import DomainConfig

logger = logging.getLogger(__name__)


@dataclass
class MatchResult:
    """Result of a matching operation."""

    matched: bool
    """Whether a match was found."""

    node_id: Optional[str] = None
    """Neo4j internal node ID if matched."""

    properties: Optional[Dict[str, Any]] = None
    """Properties of the matched node."""

    score: Optional[float] = None
    """Match score (for similarity matching, 0.0 to 1.0)."""

    match_type: str = "none"
    """Type of match: 'exact', 'similarity', or 'none'."""


@dataclass
class DiscoveredRelationship:
    """A discovered relationship between entities."""

    type_name: str
    """Relationship type (e.g., 'ABOUT', 'HAS_ANSWER')."""

    source_label: str
    """Label of the source node."""

    source_key: Dict[str, Any]
    """Properties to match the source node."""

    target_label: str
    """Label of the target node."""

    target_key: Dict[str, Any]
    """Properties to match the target node."""

    target_node_id: Optional[str] = None
    """Neo4j element ID of target (if already exists)."""

    properties: Dict[str, Any] = field(default_factory=dict)
    """Properties to set on the relationship."""

    confidence: float = 1.0
    """Confidence score for this relationship (0.0 to 1.0)."""


class RelationshipDiscoverer:
    """Discovers relationships between entities.

    This class handles:
    - Finding existing nodes that match extracted entities
    - Creating relationships between Question and its ABOUT targets
    - Linking Questions to Answers via HAS_ANSWER
    - Connecting content to Topics via HAS_TOPIC
    - Finding RELATED_TO connections via similarity
    """

    def __init__(
        self,
        domain_config: Optional["DomainConfig"] = None,
        similarity_threshold: float = 0.7,
    ):
        """Initialize relationship discoverer.

        Args:
            domain_config (Optional['DomainConfig']): Domain configuration for relationship types.
            similarity_threshold (float): Threshold for similarity-based relationships."""
        self.domain_config = domain_config
        self.similarity_threshold = similarity_threshold

    async def discover(
        self,
        entities: List[ExtractedEntity],
        client: Any,
    ) -> List[DiscoveredRelationship]:
        """Discover relationships between extracted entities.

        Args:
            entities (List[ExtractedEntity]): List of extracted entities.
            client (Any): Neo4j client for queries.
        Returns:
            List[DiscoveredRelationship]: List of discovered relationships.
        """
        relationships = []

        # Categorize entities by type
        entities_by_label = self._categorize_entities(entities)

        # Discover Question -> Answer relationships
        q_to_a = await self._discover_question_answer_rels(entities_by_label)
        relationships.extend(q_to_a)

        # Discover Question/Answer -> Topic relationships
        to_topic = await self._discover_topic_rels(entities_by_label, client)
        relationships.extend(to_topic)

        # Discover ABOUT relationships (Question -> Function/Class/File)
        about_rels = await self._discover_about_rels(entities_by_label, client)
        relationships.extend(about_rels)

        # Discover RELATED_TO relationships via similarity
        related_rels = await self._discover_related_rels(entities_by_label, client)
        relationships.extend(related_rels)

        return relationships

    def _categorize_entities(
        self, entities: List[ExtractedEntity]
    ) -> Dict[str, List[ExtractedEntity]]:
        """Categorize entities by their label."""
        by_label: Dict[str, List[ExtractedEntity]] = {}
        for entity in entities:
            if entity.label not in by_label:
                by_label[entity.label] = []
            by_label[entity.label].append(entity)
        return by_label

    async def _discover_question_answer_rels(
        self, entities_by_label: Dict[str, List[ExtractedEntity]]
    ) -> List[DiscoveredRelationship]:
        """Discover HAS_ANSWER relationships."""
        relationships = []

        questions = entities_by_label.get("Question", [])
        answers = entities_by_label.get("Answer", [])

        # Link each answer to the question (assuming they're from same extraction)
        for question in questions:
            question_hash = question.properties.get("question_hash")
            if not question_hash:
                continue

            for answer in answers:
                answer_id = answer.properties.get("answer_id")
                if not answer_id:
                    continue

                relationships.append(
                    DiscoveredRelationship(
                        type_name="HAS_ANSWER",
                        source_label="Question",
                        source_key={"question_hash": question_hash},
                        target_label="Answer",
                        target_key={"answer_id": answer_id},
                        properties={"is_primary": True},
                    )
                )

        return relationships

    async def _discover_topic_rels(
        self,
        entities_by_label: Dict[str, List[ExtractedEntity]],
        client: Any,
    ) -> List[DiscoveredRelationship]:
        """Discover HAS_TOPIC relationships."""
        relationships = []

        topics = entities_by_label.get("Topic", [])
        questions = entities_by_label.get("Question", [])
        answers = entities_by_label.get("Answer", [])

        # Link questions to topics
        for question in questions:
            question_hash = question.properties.get("question_hash")
            if not question_hash:
                continue

            for topic in topics:
                topic_name = topic.properties.get("name")
                if not topic_name:
                    continue

                relationships.append(
                    DiscoveredRelationship(
                        type_name="HAS_TOPIC",
                        source_label="Question",
                        source_key={"question_hash": question_hash},
                        target_label="Topic",
                        target_key={"name": topic_name},
                        properties={"relevance": topic.extraction_confidence},
                    )
                )

        # Link answers to topics
        for answer in answers:
            answer_id = answer.properties.get("answer_id")
            if not answer_id:
                continue

            for topic in topics:
                topic_name = topic.properties.get("name")
                if not topic_name:
                    continue

                relationships.append(
                    DiscoveredRelationship(
                        type_name="HAS_TOPIC",
                        source_label="Answer",
                        source_key={"answer_id": answer_id},
                        target_label="Topic",
                        target_key={"name": topic_name},
                        properties={"relevance": topic.extraction_confidence},
                    )
                )

        return relationships

    async def _discover_about_rels(
        self,
        entities_by_label: Dict[str, List[ExtractedEntity]],
        client: Any,
    ) -> List[DiscoveredRelationship]:
        """Discover ABOUT relationships (Question -> Function/Class/File)."""
        relationships = []

        questions = entities_by_label.get("Question", [])
        functions = entities_by_label.get("Function", [])
        classes = entities_by_label.get("Class", [])
        files = entities_by_label.get("File", [])
        topics = entities_by_label.get("Topic", [])

        for question in questions:
            question_hash = question.properties.get("question_hash")
            if not question_hash:
                continue

            # Link to functions
            for func in functions:
                func_name = func.properties.get("name")
                if func_name:
                    # Try to find existing function in graph
                    existing = await self._find_existing_node(
                        client, "Function", {"name": func_name}
                    )
                    relationships.append(
                        DiscoveredRelationship(
                            type_name="ABOUT",
                            source_label="Question",
                            source_key={"question_hash": question_hash},
                            target_label="Function",
                            target_key={"name": func_name},
                            target_node_id=existing.node_id if existing else None,
                            properties={"confidence": func.extraction_confidence},
                        )
                    )

            # Link to classes
            for cls in classes:
                cls_name = cls.properties.get("name")
                if cls_name:
                    existing = await self._find_existing_node(
                        client, "Class", {"name": cls_name}
                    )
                    relationships.append(
                        DiscoveredRelationship(
                            type_name="ABOUT",
                            source_label="Question",
                            source_key={"question_hash": question_hash},
                            target_label="Class",
                            target_key={"name": cls_name},
                            target_node_id=existing.node_id if existing else None,
                            properties={"confidence": cls.extraction_confidence},
                        )
                    )

            # Link to files
            for file in files:
                file_path = file.properties.get("path")
                if file_path:
                    existing = await self._find_existing_node(
                        client, "File", {"path": file_path}
                    )
                    relationships.append(
                        DiscoveredRelationship(
                            type_name="ABOUT",
                            source_label="Question",
                            source_key={"question_hash": question_hash},
                            target_label="File",
                            target_key={"path": file_path},
                            target_node_id=existing.node_id if existing else None,
                            properties={"confidence": file.extraction_confidence},
                        )
                    )

            # Link to topics
            for topic in topics:
                topic_name = topic.properties.get("name")
                if topic_name:
                    relationships.append(
                        DiscoveredRelationship(
                            type_name="ABOUT",
                            source_label="Question",
                            source_key={"question_hash": question_hash},
                            target_label="Topic",
                            target_key={"name": topic_name},
                            properties={"confidence": topic.extraction_confidence},
                        )
                    )

        return relationships

    async def _discover_related_rels(
        self,
        entities_by_label: Dict[str, List[ExtractedEntity]],
        client: Any,
    ) -> List[DiscoveredRelationship]:
        """Discover RELATED_TO relationships via similarity search."""
        relationships = []

        questions = entities_by_label.get("Question", [])

        for question in questions:
            question_hash = question.properties.get("question_hash")
            embedding = question.embedding

            if not question_hash or not embedding:
                continue

            # Find similar existing questions
            similar = await self._find_similar_nodes(
                client,
                "Question",
                embedding,
                self.similarity_threshold,
                exclude_hash=question_hash,
            )

            for match in similar[:3]:  # Limit to top 3
                relationships.append(
                    DiscoveredRelationship(
                        type_name="RELATED_TO",
                        source_label="Question",
                        source_key={"question_hash": question_hash},
                        target_label="Question",
                        target_key={
                            "question_hash": match.properties.get("question_hash")
                        },
                        target_node_id=match.node_id,
                        properties={
                            "similarity": match.score,
                            "relationship_type": "similar",
                        },
                        confidence=match.score,
                    )
                )

        return relationships

    async def _find_existing_node(
        self,
        client: Any,
        label: str,
        match_props: Dict[str, Any],
    ) -> Optional[MatchResult]:
        """Find an existing node by properties."""
        where_parts = []
        params = {}

        for i, (key, value) in enumerate(match_props.items()):
            param_name = f"p{i}"
            where_parts.append(f"n.{key} = ${param_name}")
            params[param_name] = value

        if not where_parts:
            return None

        query = f"""
        MATCH (n:{label})
        WHERE {" AND ".join(where_parts)}
        RETURN elementId(n) as node_id, properties(n) as props
        LIMIT 1
        """

        try:
            result = await client.run_query(query, params)
            if result and len(result) > 0:
                row = result[0]
                return MatchResult(
                    matched=True,
                    node_id=row["node_id"],
                    properties=row["props"],
                    score=1.0,
                    match_type="exact",
                )
        except Exception as e:
            logger.debug(f"Node lookup failed: {e}")

        return None

    async def _find_similar_nodes(
        self,
        client: Any,
        label: str,
        embedding: List[float],
        threshold: float,
        exclude_hash: Optional[str] = None,
    ) -> List[MatchResult]:
        """Find similar nodes using vector search."""
        index_name = f"{label.lower()}_embedding_index"

        query = """
        CALL db.index.vector.queryNodes($index_name, $top_k, $embedding)
        YIELD node, score
        WHERE score >= $threshold
        """

        if exclude_hash:
            query += " AND node.question_hash <> $exclude_hash"

        query += """
        RETURN elementId(node) as node_id, properties(node) as props, score
        ORDER BY score DESC
        LIMIT $top_k
        """

        params = {
            "index_name": index_name,
            "embedding": embedding,
            "threshold": threshold,
            "top_k": 5,
        }
        if exclude_hash:
            params["exclude_hash"] = exclude_hash

        try:
            result = await client.run_query(query, params)
            matches = []
            for row in result or []:
                matches.append(
                    MatchResult(
                        matched=True,
                        node_id=row["node_id"],
                        properties=row["props"],
                        score=row["score"],
                        match_type="similarity",
                    )
                )
            return matches
        except Exception as e:
            logger.debug(f"Similarity search failed: {e}")
            return []
