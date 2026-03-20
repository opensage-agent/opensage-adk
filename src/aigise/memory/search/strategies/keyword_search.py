"""Keyword-based search strategy using full-text indexes."""

from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

from opensage.memory.search.strategies.base_strategy import (
    SearchContext,
    SearchResultItem,
    SearchStrategy,
)

logger = logging.getLogger(__name__)


class KeywordSearchStrategy(SearchStrategy):
    """Search strategy using keyword matching and full-text search.

    This strategy uses Neo4j full-text indexes for fast keyword matching.
    It's best for queries with specific terms, function names, or identifiers.
    """

    def __init__(self, fuzzy: bool = True, boost_exact: float = 2.0):
        """Initialize keyword search strategy.

        Args:
            fuzzy (bool): Whether to enable fuzzy matching for typos.
            boost_exact (float): Score multiplier for exact matches."""
        self.fuzzy = fuzzy
        self.boost_exact = boost_exact

    def _prepare_query_terms(self, query: str) -> str:
        """Prepare query string for full-text search.

        Handles special characters and constructs a Lucene-compatible query.
        """
        # Extract meaningful terms
        terms = re.findall(r"\w+", query.lower())
        if not terms:
            return query

        # Build OR query with optional fuzzy matching
        if self.fuzzy:
            # Use fuzzy matching (~) for each term
            fuzzy_terms = [f"{term}~" for term in terms]
            return " OR ".join(fuzzy_terms)
        else:
            return " OR ".join(terms)

    async def search(
        self,
        context: SearchContext,
        client: Any,
    ) -> List[SearchResultItem]:
        """Execute keyword search across node types."""
        results: List[SearchResultItem] = []

        # Determine node types to search
        node_types = context.node_types
        if node_types is None:
            # Search common text-containing node types
            node_types = ["Question", "Answer", "Text", "Topic", "Function", "Class"]

        search_terms = self._prepare_query_terms(context.query)

        for node_label in node_types:
            try:
                type_results = await self._search_node_type(
                    client=client,
                    node_label=node_label,
                    search_terms=search_terms,
                    original_query=context.query,
                    max_results=context.max_results,
                )
                results.extend(type_results)
            except Exception as e:
                logger.debug(f"Keyword search failed for {node_label}: {e}")
                continue

        # Sort by score and limit
        results.sort(key=lambda x: x.score, reverse=True)
        return results[: context.max_results]

    async def _search_node_type(
        self,
        client: Any,
        node_label: str,
        search_terms: str,
        original_query: str,
        max_results: int,
    ) -> List[SearchResultItem]:
        """Search a specific node type using full-text or CONTAINS."""
        # First try full-text index if available
        try:
            return await self._fulltext_search(
                client, node_label, search_terms, original_query, max_results
            )
        except Exception:
            pass

        # Fall back to CONTAINS search
        return await self._contains_search(
            client, node_label, original_query, max_results
        )

    async def _fulltext_search(
        self,
        client: Any,
        node_label: str,
        search_terms: str,
        original_query: str,
        max_results: int,
    ) -> List[SearchResultItem]:
        """Search using Neo4j full-text index."""
        index_name = f"{node_label.lower()}_fulltext_index"

        query = """
        CALL db.index.fulltext.queryNodes($index_name, $search_terms)
        YIELD node, score
        RETURN labels(node) as labels,
               elementId(node) as node_id,
               properties(node) as props,
               score
        ORDER BY score DESC
        LIMIT $limit
        """

        result = await client.run_query(
            query,
            {
                "index_name": index_name,
                "search_terms": search_terms,
                "limit": max_results,
            },
        )

        items = []
        for row in result or []:
            labels = row.get("labels", [node_label])
            primary_label = labels[0] if labels else node_label
            props = row["props"]

            # Boost exact matches
            score = row["score"]
            for value in props.values():
                if isinstance(value, str) and original_query.lower() in value.lower():
                    score *= self.boost_exact
                    break

            items.append(
                SearchResultItem(
                    node_label=primary_label,
                    node_id=row["node_id"],
                    properties=props,
                    score=min(score / 10.0, 1.0),  # Normalize score
                    match_type="keyword",
                    highlight=self._get_highlight(props, original_query),
                )
            )
        return items

    async def _contains_search(
        self,
        client: Any,
        node_label: str,
        query_text: str,
        max_results: int,
    ) -> List[SearchResultItem]:
        """Fall back to CONTAINS search if no full-text index."""
        # Determine text property to search based on node type
        text_props = {
            "Question": "text",
            "Answer": "text",
            "Text": "text",
            "Topic": "name",
            "Function": "name",
            "Class": "name",
            "File": "path",
        }
        text_prop = text_props.get(node_label, "text")

        query = f"""
        MATCH (n:{node_label})
        WHERE toLower(n.{text_prop}) CONTAINS toLower($search_text)
        RETURN labels(n) as labels,
               elementId(n) as node_id,
               properties(n) as props
        LIMIT $limit
        """

        try:
            result = await client.run_query(
                query,
                {"search_text": query_text, "limit": max_results},
            )
        except Exception as e:
            logger.debug(f"Contains search failed: {e}")
            return []

        items = []
        for row in result or []:
            labels = row.get("labels", [node_label])
            primary_label = labels[0] if labels else node_label
            props = row["props"]

            items.append(
                SearchResultItem(
                    node_label=primary_label,
                    node_id=row["node_id"],
                    properties=props,
                    score=0.6,  # Moderate confidence for CONTAINS match
                    match_type="keyword",
                    highlight=self._get_highlight(props, query_text),
                )
            )
        return items

    def _get_highlight(self, props: dict, query: str) -> Optional[str]:
        """Extract a highlighted context snippet around the match."""
        query_lower = query.lower()
        for value in props.values():
            if isinstance(value, str) and query_lower in value.lower():
                idx = value.lower().find(query_lower)
                start = max(0, idx - 30)
                end = min(len(value), idx + len(query) + 30)
                snippet = value[start:end]
                if start > 0:
                    snippet = "..." + snippet
                if end < len(value):
                    snippet = snippet + "..."
                return snippet
        return None

    def get_name(self) -> str:
        return "keyword_search"

    def get_description(self) -> str:
        return "Keyword-based search using full-text indexes"

    async def can_handle_query(self, query: str, context: SearchContext) -> float:
        """Keyword search is best for specific terms and identifiers."""
        # Good for short, specific queries
        word_count = len(query.split())

        # Check for code-like identifiers
        has_identifier = bool(re.search(r"[A-Za-z_][A-Za-z0-9_]*", query))
        has_camelcase = bool(re.search(r"[a-z][A-Z]", query))
        has_underscore = "_" in query

        if has_camelcase or has_underscore:
            return 0.9  # Very likely looking for code identifiers
        elif has_identifier and word_count <= 3:
            return 0.8
        elif word_count <= 2:
            return 0.7
        else:
            return 0.4  # Longer queries better suited for embedding search
