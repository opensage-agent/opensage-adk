"""Search strategies for the memory system."""

from opensage.memory.search.strategies.base_strategy import SearchStrategy
from opensage.memory.search.strategies.embedding_search import EmbeddingSearchStrategy
from opensage.memory.search.strategies.keyword_search import KeywordSearchStrategy
from opensage.memory.search.strategies.title_browse import TitleBrowseStrategy

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
