"""Search interface for the memory system."""

from aigise.memory.search.search_controller import MemorySearchController, SearchResult
from aigise.memory.search.strategies.base_strategy import SearchStrategy
from aigise.memory.search.strategies.embedding_search import EmbeddingSearchStrategy
from aigise.memory.search.strategies.keyword_search import KeywordSearchStrategy
from aigise.memory.search.strategies.title_browse import TitleBrowseStrategy

__all__ = [
    "MemorySearchController",
    "SearchResult",
    "SearchStrategy",
    "EmbeddingSearchStrategy",
    "KeywordSearchStrategy",
    "TitleBrowseStrategy",
]
