"""Matching strategy implementations for entity resolution."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from aigise.memory.schema.node_types import NodeTypeConfig

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


class MatchingStrategy(ABC):
    """Abstract base class for node matching strategies."""

    @abstractmethod
    async def match(
        self,
        node_config: "NodeTypeConfig",
        properties: Dict[str, Any],
        client: Any,
    ) -> List[MatchResult]:
        """Find matching nodes in the graph.

        Args:
            node_config: Configuration for the node type being matched.
            properties: Properties to match against.
            client: Neo4j client for executing queries.

        Returns:
            List of match results, ordered by relevance.
        """
        pass

    @abstractmethod
    def get_strategy_name(self) -> str:
        """Get the name of this matching strategy."""
        pass


class ExactMatchStrategy(MatchingStrategy):
    """Match nodes based on exact property equality."""

    async def match(
        self,
        node_config: "NodeTypeConfig",
        properties: Dict[str, Any],
        client: Any,
    ) -> List[MatchResult]:
        """Find nodes with exactly matching properties."""
        merge_keys = node_config.get_merge_keys()
        if not merge_keys:
            logger.warning(f"No merge keys defined for {node_config.label}")
            return []

        # Build WHERE clause for exact matching
        where_parts = []
        params = {}
        for i, key in enumerate(merge_keys):
            if key in properties:
                param_name = f"prop_{i}"
                where_parts.append(f"n.{key} = ${param_name}")
                params[param_name] = properties[key]

        if not where_parts:
            return []

        where_clause = " AND ".join(where_parts)
        query = f"""
        MATCH (n:{node_config.label})
        WHERE {where_clause}
        RETURN elementId(n) as node_id, properties(n) as props
        LIMIT 10
        """

        try:
            result = await client.run_query(query, params)
            matches = []
            for row in result or []:
                matches.append(
                    MatchResult(
                        matched=True,
                        node_id=row["node_id"],
                        properties=row["props"],
                        score=1.0,
                        match_type="exact",
                    )
                )
            return matches
        except Exception as e:
            logger.error(f"Exact match query failed: {e}")
            return []

    def get_strategy_name(self) -> str:
        return "exact"


class SimilarityMatchStrategy(MatchingStrategy):
    """Match nodes based on embedding similarity."""

    def __init__(self, index_name: Optional[str] = None):
        """Initialize with optional vector index name.

        Args:
            index_name: Name of the Neo4j vector index. If not provided,
                       will be constructed from node label.
        """
        self.index_name = index_name

    async def match(
        self,
        node_config: "NodeTypeConfig",
        properties: Dict[str, Any],
        client: Any,
    ) -> List[MatchResult]:
        """Find nodes with similar embeddings."""
        if not node_config.supports_similarity_search():
            logger.warning(f"{node_config.label} does not support similarity search")
            return []

        embedding_prop = node_config.embedding_property
        if embedding_prop not in properties:
            logger.warning(f"No embedding provided for similarity search")
            return []

        embedding = properties[embedding_prop]
        threshold = node_config.similarity_threshold

        # Construct index name if not provided
        index_name = self.index_name or f"{node_config.label.lower()}_embedding_index"

        query = """
        CALL db.index.vector.queryNodes($index_name, $top_k, $embedding)
        YIELD node, score
        WHERE score >= $threshold
        RETURN elementId(node) as node_id, properties(node) as props, score
        ORDER BY score DESC
        """

        try:
            result = await client.run_query(
                query,
                {
                    "index_name": index_name,
                    "embedding": embedding,
                    "top_k": 10,
                    "threshold": threshold,
                },
            )
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
            logger.error(f"Similarity match query failed: {e}")
            return []

    def get_strategy_name(self) -> str:
        return "similarity"


class HybridMatchStrategy(MatchingStrategy):
    """Try exact match first, fall back to similarity."""

    def __init__(self):
        self.exact_strategy = ExactMatchStrategy()
        self.similarity_strategy = SimilarityMatchStrategy()

    async def match(
        self,
        node_config: "NodeTypeConfig",
        properties: Dict[str, Any],
        client: Any,
    ) -> List[MatchResult]:
        """Try exact match first, then similarity."""
        # Try exact match first
        exact_matches = await self.exact_strategy.match(node_config, properties, client)
        if exact_matches:
            return exact_matches

        # Fall back to similarity if supported
        if node_config.supports_similarity_search():
            return await self.similarity_strategy.match(node_config, properties, client)

        return []

    def get_strategy_name(self) -> str:
        return "hybrid"


def get_matching_strategy(
    node_config: "NodeTypeConfig",
) -> MatchingStrategy:
    """Get the appropriate matching strategy for a node type.

    Args:
        node_config: Configuration for the node type.

    Returns:
        Appropriate matching strategy instance.
    """
    from aigise.memory.schema.node_types import MatchingRule

    if node_config.matching_rule == MatchingRule.EXACT:
        return ExactMatchStrategy()
    elif node_config.matching_rule == MatchingRule.SIMILARITY:
        return SimilarityMatchStrategy()
    elif node_config.matching_rule == MatchingRule.HYBRID:
        return HybridMatchStrategy()
    else:
        # Default to exact matching
        return ExactMatchStrategy()
