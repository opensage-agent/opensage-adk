"""Base class for search strategies."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from opensage.memory.config.domain_config import DomainConfig

logger = logging.getLogger(__name__)


@dataclass
class SearchResultItem:
    """A single search result item."""

    node_label: str
    """The Neo4j label of the matched node."""

    node_id: str
    """The Neo4j element ID of the matched node."""

    properties: Dict[str, Any]
    """Properties of the matched node."""

    score: float = 1.0
    """Relevance score (0.0 to 1.0)."""

    match_type: str = "exact"
    """Type of match: 'exact', 'similarity', 'keyword', 'browse'."""

    highlight: Optional[str] = None
    """Highlighted match context (for keyword search)."""

    def get_display_text(self) -> str:
        """Get a human-readable display text for this result."""
        # Try common display properties in order
        for prop_name in ["text", "name", "question", "title", "path"]:
            if prop_name in self.properties:
                return str(self.properties[prop_name])[:200]
        return f"{self.node_label}:{self.node_id}"


@dataclass
class SearchContext:
    """Context for a search operation."""

    query: str
    """The search query."""

    node_types: Optional[List[str]] = None
    """Restrict search to specific node types (None = all)."""

    domain_config: Optional["DomainConfig"] = None
    """Domain configuration to use."""

    max_results: int = 10
    """Maximum number of results to return."""

    min_score: float = 0.0
    """Minimum score threshold."""

    metadata: Dict[str, Any] = field(default_factory=dict)
    """Additional context metadata."""


class SearchStrategy(ABC):
    """Abstract base class for search strategies.

    A search strategy defines how to search for relevant information
    in the memory graph. Different strategies may use vector similarity,
    keyword matching, graph traversal, etc.
    """

    @abstractmethod
    async def search(
        self,
        context: SearchContext,
        client: Any,
    ) -> List[SearchResultItem]:
        """Execute the search strategy.

        Args:
            context (SearchContext): Search context with query and parameters.
            client (Any): Neo4j client for executing queries.
        Returns:
            List[SearchResultItem]: List of search result items, ordered by relevance.
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """Get the unique name of this strategy."""
        pass

    @abstractmethod
    def get_description(self) -> str:
        """Get a human-readable description of this strategy."""
        pass

    def supports_node_type(self, node_type: str) -> bool:
        """Check if this strategy supports a particular node type.

        Override in subclasses to restrict to specific node types.

        Args:
            node_type (str): Node label to check.
        Returns:
            bool: True if this strategy can search this node type.
        """
        return True

    def get_supported_node_types(self) -> Optional[List[str]]:
        """Get the list of node types this strategy supports.

        Returns:
            Optional[List[str]]: List of supported node types, or None for all types.
        """
        return None

    async def can_handle_query(self, query: str, context: SearchContext) -> float:
        """Estimate how well this strategy can handle a query.

        This method helps the search controller select the best strategy.
        Override to provide better estimates based on query characteristics.

        Args:
            query (str): The search query.
            context (SearchContext): Search context.
        Returns:
            float: Confidence score from 0.0 (cannot handle) to 1.0 (ideal match).
        """
        return 0.5  # Default: moderate confidence
