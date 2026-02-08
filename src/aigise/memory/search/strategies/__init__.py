"""Search strategies for the memory system."""

from aigise.memory.search.strategies.base_strategy import SearchStrategy
from aigise.memory.search.strategies.embedding_search import EmbeddingSearchStrategy
from aigise.memory.search.strategies.keyword_search import KeywordSearchStrategy
from aigise.memory.search.strategies.title_browse import TitleBrowseStrategy

STRATEGY_REGISTRY = {
    "embedding_search": EmbeddingSearchStrategy,
    "keyword_search": KeywordSearchStrategy,
    "title_browse": TitleBrowseStrategy,
}


def get_strategy(name: str) -> type:
    """Get a strategy class by name."""
    return STRATEGY_REGISTRY.get(name)


__all__ = [
    "SearchStrategy",
    "EmbeddingSearchStrategy",
    "KeywordSearchStrategy",
    "TitleBrowseStrategy",
    "STRATEGY_REGISTRY",
    "get_strategy",
]
