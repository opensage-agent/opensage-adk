"""Base class for search strategies."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from opensage.memory.database_based.long_term.config.domain_config import (
        DomainConfig,
    )

logger = logging.getLogger(__name__)


@dataclass
class SearchResultItem:
    """A single search result item."""

    node_label: str
    node_id: str
    properties: Dict[str, Any]
    score: float = 1.0
    match_type: str = "exact"
    highlight: Optional[str] = None

    def get_display_text(self) -> str:
        for prop_name in ["text", "name", "question", "title", "path"]:
            if prop_name in self.properties:
                return str(self.properties[prop_name])[:200]
        return f"{self.node_label}:{self.node_id}"


@dataclass
class SearchContext:
    """Context for a search operation."""

    query: str
    node_types: Optional[List[str]] = None
    domain_config: Optional["DomainConfig"] = None
    max_results: int = 10
    min_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class SearchStrategy(ABC):
    """Abstract base class for search strategies."""

    @abstractmethod
    async def search(
        self,
        context: SearchContext,
        client: Any,
    ) -> List[SearchResultItem]:
        """Execute the search strategy."""

    @abstractmethod
    def get_name(self) -> str:
        """Get the unique name of this strategy."""

    @abstractmethod
    def get_description(self) -> str:
        """Get a human-readable description of this strategy."""

    def supports_node_type(self, node_type: str) -> bool:
        return True

    def get_supported_node_types(self) -> Optional[List[str]]:
        return None

    async def can_handle_query(self, query: str, context: SearchContext) -> float:
        return 0.5
