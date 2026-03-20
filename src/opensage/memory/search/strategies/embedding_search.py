"""Embedding-based similarity search strategy."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import litellm

from opensage.memory.config.memory_settings import get_memory_settings
from opensage.memory.search.strategies.base_strategy import (
    SearchContext,
    SearchResultItem,
    SearchStrategy,
)

logger = logging.getLogger(__name__)

# Embedding dimension for gemini-embedding-001
EMBEDDING_DIMENSION = 3072


async def _generate_embedding(text: str) -> List[float]:
    """Generate embedding for text using configured embedding model.

    Args:
        text (str): Text to generate embedding for.
    Returns:
        List[float]: List of floats representing the embedding vector.
    """

    settings = get_memory_settings()
    response = await litellm.aembedding(
        model=settings.embedding_model,
        input=text,
    )
    return response.data[0]["embedding"]


class EmbeddingSearchStrategy(SearchStrategy):
    """Search strategy using vector embeddings for semantic similarity.

    This strategy generates embeddings for the query and finds similar
    nodes using Neo4j's vector index capabilities.
    """

    def __init__(
        self,
        similarity_threshold: float = 0.7,
        index_suffix: str = "_embedding_index",
    ):
        """Initialize embedding search strategy.

        Args:
            similarity_threshold (float): Minimum similarity score to include results.
            index_suffix (str): Suffix for vector index names (e.g., 'question_embedding_index')."""
        self.similarity_threshold = similarity_threshold
        self.index_suffix = index_suffix
        self._embedding_cache: Dict[str, List[float]] = {}

    async def _get_embedding(self, text: str) -> List[float]:
        """Get embedding for text, using cache if available."""
        if text not in self._embedding_cache:
            self._embedding_cache[text] = await _generate_embedding(text)
        return self._embedding_cache[text]

    def _get_index_name(self, node_label: str) -> str:
        """Get the vector index name for a node label."""
        return f"{node_label.lower()}{self.index_suffix}"

    async def search(
        self,
        context: SearchContext,
        client: Any,
    ) -> List[SearchResultItem]:
        """Execute embedding similarity search."""
        results: List[SearchResultItem] = []

        # Generate embedding for query
        try:
            query_embedding = await self._get_embedding(context.query)
        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            return []

        # Determine which node types to search
        node_types = context.node_types
        if node_types is None and context.domain_config:
            node_types = context.domain_config.get_similarity_searchable_types()
        if not node_types:
            # Default to Question if no types specified
            node_types = ["Question"]

        # Search each node type with embedding index
        for node_label in node_types:
            try:
                index_name = self._get_index_name(node_label)
                type_results = await self._search_node_type(
                    client=client,
                    node_label=node_label,
                    index_name=index_name,
                    embedding=query_embedding,
                    threshold=max(self.similarity_threshold, context.min_score),
                    top_k=context.max_results,
                )
                results.extend(type_results)
            except Exception as e:
                logger.warning(f"Embedding search failed for {node_label}: {e}")
                continue

        # Sort by score and limit results
        results.sort(key=lambda x: x.score, reverse=True)
        return results[: context.max_results]

    async def _search_node_type(
        self,
        client: Any,
        node_label: str,
        index_name: str,
        embedding: List[float],
        threshold: float,
        top_k: int,
    ) -> List[SearchResultItem]:
        """Search a specific node type using vector similarity."""
        query = """
        CALL db.index.vector.queryNodes($index_name, $top_k, $embedding)
        YIELD node, score
        WHERE score >= $threshold
        RETURN labels(node) as labels,
               elementId(node) as node_id,
               properties(node) as props,
               score
        ORDER BY score DESC
        """

        try:
            result = await client.run_query(
                query,
                {
                    "index_name": index_name,
                    "embedding": embedding,
                    "top_k": top_k,
                    "threshold": threshold,
                },
            )
        except Exception as e:
            logger.debug(f"Vector search query failed for {node_label}: {e}")
            return []

        items = []
        for row in result or []:
            labels = row.get("labels", [node_label])
            primary_label = labels[0] if labels else node_label
            items.append(
                SearchResultItem(
                    node_label=primary_label,
                    node_id=row["node_id"],
                    properties=row["props"],
                    score=row["score"],
                    match_type="similarity",
                )
            )
        return items

    def get_name(self) -> str:
        return "embedding_search"

    def get_description(self) -> str:
        return "Semantic similarity search using vector embeddings"

    def get_supported_node_types(self) -> Optional[List[str]]:
        """Embedding search works best with types that have embeddings."""
        return [
            "Question",
            "Text",
        ]  # Types with embeddings (Topic is for categorization only)

    async def can_handle_query(self, query: str, context: SearchContext) -> float:
        """Embedding search is best for semantic/conceptual queries."""
        # Good for longer, more semantic queries
        word_count = len(query.split())
        if word_count >= 5:
            return 0.9
        elif word_count >= 3:
            return 0.7
        else:
            # Short queries might be better handled by keyword search
            return 0.5
