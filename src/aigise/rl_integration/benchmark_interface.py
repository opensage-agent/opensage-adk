"""
Benchmark interface for RL framework integration.

Each benchmark module should export functions that this interface wraps
to provide a consistent API for adapters.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class BenchmarkInterface:
    """Interface for benchmark-specific logic in RL integration.

    Wraps benchmark module functions to provide a consistent API for adapters.

    Each benchmark module (e.g., secodeplt) should export:
    - get_prompt(sample) -> str: Extract prompt from sample
    - reward_func(args, sample, **kwargs) -> dict: Calculate reward

    Optional exports:
    - preprocess_sample(sample) -> sample: Preprocess before agent execution
    - postprocess_response(sample, response) -> sample: Postprocess after agent

    Usage:
        interface = BenchmarkInterface.load("secodeplt")
        prompt = interface.get_prompt(sample)
        reward = await interface.reward_func(args, sample)
    """

    def __init__(
        self,
        get_prompt_fn: Optional[Callable[[Any], str]] = None,
        reward_fn: Optional[Callable[..., Any]] = None,
        preprocess_fn: Optional[Callable[[Any], Any]] = None,
        postprocess_fn: Optional[Callable[[Any, str], Any]] = None,
        evaluation_class: Optional[type] = None,
    ):
        """Initialize benchmark interface.

        Args:
            get_prompt_fn: Function to extract prompt from sample
            reward_fn: Function to calculate reward
            preprocess_fn: Optional function to preprocess sample
            postprocess_fn: Optional function to postprocess response
            evaluation_class: The registered Evaluation subclass
        """
        self._get_prompt_fn = get_prompt_fn
        self._reward_fn = reward_fn
        self._preprocess_fn = preprocess_fn
        self._postprocess_fn = postprocess_fn
        self.evaluation_class = evaluation_class

    @classmethod
    def load(cls, benchmark_name: str) -> "BenchmarkInterface":
        """Load benchmark interface from registered Evaluation class.

        Looks up the benchmark by name from the Evaluation registry.
        Evaluation subclasses are auto-registered when their module is imported.

        RL integration methods (get_prompt, reward_func, etc.) are called
        directly on the Evaluation class.

        Args:
            benchmark_name: Name of the benchmark (case-insensitive, e.g., "secodeplt")

        Returns:
            BenchmarkInterface instance

        Raises:
            ImportError: If benchmark not found in registry
        """
        from aigise.evaluation.base import _EVALUATION_REGISTRY, get_evaluation_class

        # First, try to import common submodules to trigger registration
        base_path = f"aigise.evaluation.{benchmark_name}"
        common_submodules = [
            "vul_detection",
            "evaluation",
            "main",
            "benchmark",
            "cybergym_static",
        ]

        for submodule_name in common_submodules:
            try:
                importlib.import_module(f"{base_path}.{submodule_name}")
                logger.info(f"Loaded {benchmark_name}.{submodule_name}")
                break  # Found a module, stop searching
            except ImportError:
                continue

        # Look up the evaluation class from registry
        eval_class = get_evaluation_class(benchmark_name)
        if eval_class is None:
            available = list(_EVALUATION_REGISTRY.keys())
            raise ImportError(
                f"Benchmark '{benchmark_name}' not found in registry. "
                f"Available: {available}"
            )

        logger.info(f"Found evaluation class: {eval_class.__name__}")

        # Use class methods directly from the Evaluation class
        return cls(
            get_prompt_fn=eval_class.get_prompt,
            reward_fn=eval_class.reward_func,
            preprocess_fn=eval_class.preprocess_sample,
            postprocess_fn=eval_class.postprocess_response,
            evaluation_class=eval_class,
        )

    def get_prompt(self, sample: Any) -> str:
        """Extract prompt from sample.

        Delegates to the Evaluation class's get_prompt method.

        Args:
            sample: Sample object from RL framework

        Returns:
            Prompt string
        """
        if self._get_prompt_fn:
            return self._get_prompt_fn(sample)
        return ""

    async def reward_func(self, args: Any, sample: Any, **kwargs) -> dict:
        """Calculate reward for sample.

        Delegates to the Evaluation class's reward_func method.

        Args:
            args: Rollout arguments from RL framework
            sample: Sample with response
            **kwargs: Additional arguments

        Returns:
            Reward dict with 'score' and metadata
        """
        if self._reward_fn:
            import asyncio

            result = self._reward_fn(args, sample, **kwargs)
            if asyncio.iscoroutine(result):
                return await result
            return result
        return {"score": 0.0, "status": "no_reward_func"}

    def preprocess_sample(self, sample: Any) -> Any:
        """Preprocess sample before agent execution.

        Args:
            sample: Sample object

        Returns:
            Preprocessed sample (may be same object)
        """
        if self._preprocess_fn:
            return self._preprocess_fn(sample)
        return sample

    def postprocess_response(self, sample: Any, response: str) -> Any:
        """Postprocess agent response.

        Args:
            sample: Sample object
            response: Agent response text

        Returns:
            Updated sample
        """
        if self._postprocess_fn:
            return self._postprocess_fn(sample, response)
        return sample

    @property
    def has_get_prompt(self) -> bool:
        """Check if benchmark provides get_prompt."""
        return self._get_prompt_fn is not None

    @property
    def has_reward_func(self) -> bool:
        """Check if benchmark provides reward_func."""
        return self._reward_fn is not None

    @property
    def has_evaluation_class(self) -> bool:
        """Check if benchmark has a registered Evaluation class."""
        return self.evaluation_class is not None
