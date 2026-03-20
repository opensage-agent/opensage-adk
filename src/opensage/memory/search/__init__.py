"""Search interface for the memory system."""

from opensage.memory.search.search_controller import (
    MemorySearchController,
    SearchResult,
)
from opensage.memory.search.strategies.base_strategy import SearchStrategy
from opensage.memory.search.strategies.embedding_search import EmbeddingSearchStrategy
from opensage.memory.search.strategies.keyword_search import KeywordSearchStrategy
from opensage.memory.search.strategies.title_browse import TitleBrowseStrategy

__all__ = [
    "MemorySearchController",
    "SearchResult",
    "SearchStrategy",
    "EmbeddingSearchStrategy",
    "KeywordSearchStrategy",
    "TitleBrowseStrategy",
]
