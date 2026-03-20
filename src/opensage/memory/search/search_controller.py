"""Search controller for orchestrating memory searches."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import litellm

from opensage.memory.config.memory_settings import get_memory_settings
from opensage.memory.search.strategies import STRATEGY_REGISTRY
from opensage.memory.search.strategies.base_strategy import (
    SearchContext,
    SearchResultItem,
    SearchStrategy,
)

if TYPE_CHECKING:
    from opensage.memory.config.domain_config import DomainConfig

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Result of a memory search operation."""

    items: List[SearchResultItem] = field(default_factory=list)
    """Search result items."""

    total_found: int = 0
    """Total number of results found (before limiting)."""

    strategy_used: str = ""
    """Name of the strategy that found results."""

    iterations: int = 1
    """Number of search iterations performed."""

    sufficient: bool = False
    """Whether the results are considered sufficient."""

    metadata: Dict[str, Any] = field(default_factory=dict)
    """Additional search metadata."""

    @property
    def has_results(self) -> bool:
        """Check if any results were found."""
        return len(self.items) > 0

    def get_best_result(self) -> Optional[SearchResultItem]:
        """Get the highest-scoring result."""
        if not self.items:
            return None
        return max(self.items, key=lambda x: x.score)


class MemorySearchController:
    """Controller for orchestrating memory search operations.

    This controller:
    1. Selects appropriate search strategies (LLM-driven or heuristic)
    2. Executes searches with multi-round refinement
    3. Evaluates result sufficiency
    4. Combines results from multiple strategies if needed
    """

    def __init__(
        self,
        domain_config: Optional["DomainConfig"] = None,
        max_iterations: int = 3,
        sufficiency_threshold: int = 1,
        use_llm_selection: bool = True,
    ):
        """Initialize the search controller.

        Args:
            domain_config (Optional['DomainConfig']): Domain configuration defining available strategies.
            max_iterations (int): Maximum number of refinement iterations.
            sufficiency_threshold (int): Minimum results needed to consider sufficient.
            use_llm_selection (bool): Whether to use LLM for strategy selection."""
        self.domain_config = domain_config
        self.max_iterations = max_iterations
        self.sufficiency_threshold = sufficiency_threshold
        self.use_llm_selection = use_llm_selection
        self._strategies: Dict[str, SearchStrategy] = {}
        self._initialize_strategies()

    def _initialize_strategies(self) -> None:
        """Initialize available search strategies."""
        # Get strategies from domain config or use defaults
        strategy_names = ["embedding_search", "keyword_search", "title_browse"]
        if self.domain_config:
            strategy_names = self.domain_config.search_strategies

        for name in strategy_names:
            strategy_class = STRATEGY_REGISTRY.get(name)
            if strategy_class:
                self._strategies[name] = strategy_class()
            else:
                logger.warning(f"Unknown strategy: {name}")

    async def search(
        self,
        query: str,
        node_types: Optional[List[str]] = None,
        client: Any = None,
        max_results: int = 10,
        min_score: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SearchResult:
        """Execute a memory search.

                Args:
                    query (str): The search query.
                    node_types (Optional[List[str]]): Optional list of node types to search.
                    client (Any): Neo4j client for executing queries.
                    max_results (int): Maximum results to return.
                    min_score (float): Minimum score threshold.
                    metadata (Optional[Dict[str, Any]]): Additional search context.

        Raises:
          ValueError: Raised when this operation fails.
                Returns:
                    SearchResult: SearchResult with found items and metadata.
        """
        if client is None:
            raise ValueError("Neo4j client is required")

        context = SearchContext(
            query=query,
            node_types=node_types,
            domain_config=self.domain_config,
            max_results=max_results,
            min_score=min_score,
            metadata=metadata or {},
        )

        # Select strategy
        strategy = await self._select_strategy(context)
        logger.info(f"Selected strategy: {strategy.get_name()}")

        # Execute search with refinement
        result = await self._execute_with_refinement(context, strategy, client)

        return result

    async def _select_strategy(self, context: SearchContext) -> SearchStrategy:
        """Select the best strategy for the query.

        Uses LLM if enabled, otherwise uses heuristics.
        """
        if self.use_llm_selection:
            return await self._llm_select_strategy(context)
        else:
            return await self._heuristic_select_strategy(context)

    async def _llm_select_strategy(self, context: SearchContext) -> SearchStrategy:
        """Use LLM to intelligently select the best search strategy.

        Analyzes the query characteristics and available strategies to determine
        which approach will yield the best results.
        """
        try:
            # Build strategy descriptions
            strategy_descriptions = []
            for name, strategy in self._strategies.items():
                desc = (
                    strategy.get_description()
                    if hasattr(strategy, "get_description")
                    else name
                )
                strategy_descriptions.append(f"- {name}: {desc}")

            prompt = f"""Given the search query and available strategies, select the best one.

Query: {context.query}
Node types to search: {context.node_types}

Available strategies:
{chr(10).join(strategy_descriptions)}

Consider:
- embedding_search: Best for semantic similarity, finding related concepts, "how does X work"
- keyword_search: Best for exact matches, function/class names, file paths, specific identifiers
- title_browse: Best for browsing/exploring when query is vague or wants to see what's available

Respond with only the strategy name (one word)."""

            settings = get_memory_settings()
            response = await litellm.acompletion(
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=1,
                max_tokens=20,
            )
            selected = response.choices[0].message.content.strip().lower()

            if selected in self._strategies:
                logger.debug(f"LLM selected strategy: {selected}")
                return self._strategies[selected]
            else:
                logger.warning(
                    f"LLM selected unknown strategy '{selected}', falling back to heuristic"
                )

        except Exception as e:
            logger.warning(f"LLM strategy selection failed: {e}")

        # Fall back to heuristic
        return await self._heuristic_select_strategy(context)

    async def _heuristic_select_strategy(
        self, context: SearchContext
    ) -> SearchStrategy:
        """Use heuristics to select the best strategy."""
        best_strategy = None
        best_score = -1.0

        for strategy in self._strategies.values():
            score = await strategy.can_handle_query(context.query, context)
            if score > best_score:
                best_score = score
                best_strategy = strategy

        if best_strategy is None:
            # Fall back to default strategy
            default_name = (
                self.domain_config.default_strategy
                if self.domain_config
                else "embedding_search"
            )
            best_strategy = self._strategies.get(default_name)
            if best_strategy is None:
                # Ultimate fallback
                best_strategy = list(self._strategies.values())[0]

        return best_strategy

    async def _execute_with_refinement(
        self,
        context: SearchContext,
        strategy: SearchStrategy,
        client: Any,
    ) -> SearchResult:
        """Execute search with multi-round refinement if needed."""
        all_items: List[SearchResultItem] = []
        iterations = 0
        strategies_used = []

        current_strategy = strategy
        current_context = context

        while iterations < self.max_iterations:
            iterations += 1

            # Execute current strategy
            items = await current_strategy.search(current_context, client)
            all_items.extend(items)
            strategies_used.append(current_strategy.get_name())

            # Check sufficiency
            if self._is_sufficient(all_items, current_context):
                break

            # Try a different strategy if available
            next_strategy = await self._get_next_strategy(
                current_strategy, current_context, all_items
            )
            if next_strategy is None:
                break

            current_strategy = next_strategy

        # Deduplicate and sort results
        unique_items = self._deduplicate_results(all_items)
        unique_items.sort(key=lambda x: x.score, reverse=True)
        final_items = unique_items[: context.max_results]

        return SearchResult(
            items=final_items,
            total_found=len(unique_items),
            strategy_used=", ".join(strategies_used),
            iterations=iterations,
            sufficient=len(final_items) >= self.sufficiency_threshold,
            metadata={
                "strategies_tried": strategies_used,
                "query": context.query,
            },
        )

    def _is_sufficient(
        self, items: List[SearchResultItem], context: SearchContext
    ) -> bool:
        """Check if current results are sufficient."""
        if len(items) >= self.sufficiency_threshold:
            # Check if top results are high quality
            high_quality = [i for i in items if i.score >= 0.7]
            return len(high_quality) >= self.sufficiency_threshold
        return False

    async def _get_next_strategy(
        self,
        current: SearchStrategy,
        context: SearchContext,
        current_results: List[SearchResultItem],
    ) -> Optional[SearchStrategy]:
        """Get the next strategy to try."""
        current_name = current.get_name()

        # Simple round-robin for now
        strategy_order = ["embedding_search", "keyword_search", "title_browse"]
        if current_name in strategy_order:
            idx = strategy_order.index(current_name)
            for next_idx in range(idx + 1, len(strategy_order)):
                next_name = strategy_order[next_idx]
                if next_name in self._strategies:
                    return self._strategies[next_name]

        return None

    def _deduplicate_results(
        self, items: List[SearchResultItem]
    ) -> List[SearchResultItem]:
        """Remove duplicate results, keeping highest score."""
        seen: Dict[str, SearchResultItem] = {}
        for item in items:
            key = item.node_id
            if key not in seen or item.score > seen[key].score:
                seen[key] = item
        return list(seen.values())

    def add_strategy(self, name: str, strategy: SearchStrategy) -> None:
        """Add a custom search strategy."""
        self._strategies[name] = strategy

    def get_available_strategies(self) -> List[str]:
        """Get names of available strategies."""
        return list(self._strategies.keys())
