"""Title browsing strategy for exploring available content."""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from opensage.memory.search.strategies.base_strategy import (
    SearchContext,
    SearchResultItem,
    SearchStrategy,
)

logger = logging.getLogger(__name__)


class TitleBrowseStrategy(SearchStrategy):
    """Search strategy for browsing titles/names without a specific query.

    This strategy is useful for:
    - Exploring what's in the memory graph
    - Finding items when you know approximately what you're looking for
    - Getting a list of available topics, questions, or entities
    """

    def __init__(self, default_limit: int = 20):
        """Initialize title browse strategy.

        Args:
            default_limit (int): Default number of items to return."""
        self.default_limit = default_limit

    async def search(
        self,
        context: SearchContext,
        client: Any,
    ) -> List[SearchResultItem]:
        """Browse titles/names, optionally filtering by query prefix."""
        results: List[SearchResultItem] = []

        # Determine node types to browse
        node_types = context.node_types
        if node_types is None:
            # Browse common named types
            node_types = ["Question", "Topic", "Function", "Class"]

        for node_label in node_types:
            try:
                type_results = await self._browse_node_type(
                    client=client,
                    node_label=node_label,
                    query=context.query,
                    max_results=context.max_results or self.default_limit,
                )
                results.extend(type_results)
            except Exception as e:
                logger.debug(f"Title browse failed for {node_label}: {e}")
                continue

        # Sort by title/name alphabetically if no query
        if not context.query:
            results.sort(key=lambda x: x.get_display_text().lower())
        else:
            # Sort by score (relevance) if query provided
            results.sort(key=lambda x: x.score, reverse=True)

        return results[: context.max_results or self.default_limit]

    async def _browse_node_type(
        self,
        client: Any,
        node_label: str,
        query: Optional[str],
        max_results: int,
    ) -> List[SearchResultItem]:
        """Browse a specific node type."""
        # Determine the display property based on node type
        display_props = {
            "Question": "text",
            "Answer": "text",
            "Topic": "name",
            "Function": "name",
            "Class": "name",
            "File": "path",
        }
        display_prop = display_props.get(node_label, "name")

        if query:
            # Filter by prefix/contains
            cypher_query = f"""
            MATCH (n:{node_label})
            WHERE toLower(n.{display_prop}) STARTS WITH toLower($prefix)
               OR toLower(n.{display_prop}) CONTAINS toLower($prefix)
            RETURN labels(n) as labels,
                   elementId(n) as node_id,
                   properties(n) as props,
                   CASE
                       WHEN toLower(n.{display_prop}) STARTS WITH toLower($prefix) THEN 1.0
                       ELSE 0.7
                   END as score
            ORDER BY score DESC, n.{display_prop}
            LIMIT $limit
            """
            params = {"prefix": query, "limit": max_results}
        else:
            # Return all, ordered by recent or alphabetically
            cypher_query = f"""
            MATCH (n:{node_label})
            RETURN labels(n) as labels,
                   elementId(n) as node_id,
                   properties(n) as props,
                   0.5 as score
            ORDER BY n.created_at DESC, n.{display_prop}
            LIMIT $limit
            """
            params = {"limit": max_results}

        try:
            result = await client.run_query(cypher_query, params)
        except Exception as e:
            logger.debug(f"Browse query failed: {e}")
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
                    match_type="browse",
                )
            )
        return items

    def get_name(self) -> str:
        return "title_browse"

    def get_description(self) -> str:
        return "Browse available content by title or name"

    async def can_handle_query(self, query: str, context: SearchContext) -> float:
        """Title browse is best when exploring or with very short queries."""
        if not query or not query.strip():
            return 1.0  # Perfect for browsing without query

        word_count = len(query.split())
        if word_count == 1:
            return 0.6  # Single word could be a prefix
        else:
            return 0.3  # Multi-word queries better for other strategies
