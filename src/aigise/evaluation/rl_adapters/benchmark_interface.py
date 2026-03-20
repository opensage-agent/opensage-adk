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
            get_prompt_fn (Optional[Callable[[Any], str]]): Function to extract prompt from sample
            reward_fn (Optional[Callable[..., Any]]): Function to calculate reward
            preprocess_fn (Optional[Callable[[Any], Any]]): Optional function to preprocess sample
            postprocess_fn (Optional[Callable[[Any, str], Any]]): Optional function to postprocess response
            evaluation_class (Optional[type]): The registered Evaluation subclass"""
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
            benchmark_name (str): Name of the benchmark (case-insensitive, e.g., "secodeplt")
        Returns:
            'BenchmarkInterface': BenchmarkInterface instance

        Raises:
            ImportError: If benchmark not found in registry
        """
        import sys

        from opensage.evaluation.base import _EVALUATION_REGISTRY, get_evaluation_class
        from opensage.utils.project_info import PROJECT_PATH

        # Ensure benchmarks/ (at project root) is importable.
        project_root = str(PROJECT_PATH)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        # Import benchmark submodules to trigger Evaluation class registration.
        base_path = f"benchmarks.{benchmark_name}"
        common_submodules = [
            "vul_detection",
            "evaluation",
            "main",
            "benchmark",
            "cybergym_static",
            "mock_debug_evaluation",
        ]

        for submodule_name in common_submodules:
            try:
                importlib.import_module(f"{base_path}.{submodule_name}")
                logger.info(f"Loaded {base_path}.{submodule_name}")
                break
            except ImportError:
                continue

        # Look up the evaluation class from registry
        eval_class = get_evaluation_class(benchmark_name)

        # Fallback: class name (lowercase) may differ from package name.
        # e.g. package "mock_debug" registers class "MockDebugEvaluation" as
        # "mockdebugevaluation".  Scan registry for any class whose module
        # starts with the base_path we just imported.
        if eval_class is None:
            for reg_name, reg_cls in _EVALUATION_REGISTRY.items():
                mod = getattr(reg_cls, "__module__", "")
                if mod.startswith(base_path):
                    eval_class = reg_cls
                    logger.info(
                        f"Found evaluation class via module fallback: "
                        f"{reg_cls.__name__} (registered as '{reg_name}')"
                    )
                    break

        if eval_class is None:
            available = list(_EVALUATION_REGISTRY.keys())
            raise ImportError(
                f"Benchmark '{benchmark_name}' not found in registry. "
                f"Available: {available}"
            )

        logger.info(f"Found evaluation class: {eval_class.__name__}")

        # Use class methods directly from the Evaluation class.
        # These are optional — use getattr with None fallback.
        return cls(
            get_prompt_fn=getattr(eval_class, "get_prompt", None),
            reward_fn=getattr(eval_class, "reward_func", None),
            preprocess_fn=getattr(eval_class, "preprocess_sample", None),
            postprocess_fn=getattr(eval_class, "postprocess_response", None),
            evaluation_class=eval_class,
        )

    def get_prompt(self, sample: Any) -> str:
        """Extract prompt from sample.

        Delegates to the Evaluation class's get_prompt method.

        Args:
            sample (Any): Sample object from RL framework
        Returns:
            str: Prompt string
        """
        if self._get_prompt_fn:
            return self._get_prompt_fn(sample)
        return ""

    async def reward_func(self, args: Any, sample: Any, **kwargs) -> dict:
        """Calculate reward for sample.

        Delegates to the Evaluation class's reward_func method.

        Args:
            args (Any): Rollout arguments from RL framework
            sample (Any): Sample with response
            **kwargs: Additional arguments
        Returns:
            dict: Reward dict with 'score' and metadata
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
            sample (Any): Sample object
        Returns:
            Any: Preprocessed sample (may be same object)
        """
        if self._preprocess_fn:
            return self._preprocess_fn(sample)
        return sample

    def postprocess_response(self, sample: Any, response: str) -> Any:
        """Postprocess agent response.

        Args:
            sample (Any): Sample object
            response (str): Agent response text
        Returns:
            Any: Updated sample
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
