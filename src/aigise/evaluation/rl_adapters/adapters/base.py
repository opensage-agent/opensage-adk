"""
Base adapter for RL framework integration.

This module defines the abstract base class that all framework-specific
adapters must implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aigise.evaluation.base import Evaluation
    from aigise.evaluation.rl_adapters.benchmark_interface import BenchmarkInterface
    from aigise.session import AigiseSession


class BaseAdapter(ABC):
    """Abstract base adapter for RL framework integration.

    Each RL framework (slime, verl, areal, etc.) should have its own
    adapter that implements this interface.

    The adapter is responsible for:
    - Converting framework-specific sample to dict format for Evaluation
    - Calling Evaluation._create_task() and _generate_one()
    - Updating the sample with results in framework-expected format
    """

    def __init__(
        self,
        aigise_session: "AigiseSession",
        evaluation: "Evaluation",
        benchmark: "BenchmarkInterface",
    ):
        """Initialize adapter.

        Args:
            aigise_session: The AIgiSE session managing resources
            evaluation: The Evaluation instance to run samples
            benchmark: BenchmarkInterface for benchmark-specific logic
        """
        self.aigise_session = aigise_session
        self.evaluation = evaluation
        self.benchmark = benchmark

    @property
    def session_id(self) -> str:
        """Get the session ID."""
        return self.aigise_session.aigise_session_id

    @abstractmethod
    def convert_to_sample_dict(self, sample: Any) -> dict:
        """Convert framework-specific sample to dict format for Evaluation.

        Each adapter must implement this to convert its framework's sample format
        to the dict format expected by Evaluation._create_task().

        Args:
            sample: Framework-specific sample object

        Returns:
            Dict in format expected by Evaluation._create_task()
        """
        pass

    @abstractmethod
    async def generate(
        self,
        args: Any,
        sample: Any,
        sampling_params: dict[str, Any],
    ) -> Any:
        """Generate response using the Evaluation.

        This method should:
        1. Convert sample to dict using convert_to_sample_dict()
        2. Call evaluation._create_task(dict) to create EvaluationTask
        3. Call evaluation._generate_one(task) to run the agent
        4. Update sample with results using update_sample_success/error()

        Args:
            args: Framework-specific arguments
            sample: Framework-specific sample object
            sampling_params: Sampling parameters

        Returns:
            Updated sample object in framework-expected format
        """
        pass

    @abstractmethod
    def update_sample_success(
        self,
        sample: Any,
        result: dict,
        metadata: dict[str, Any],
    ) -> Any:
        """Update sample with successful result.

        Args:
            sample: Framework-specific sample object
            result: Result dict from Evaluation._generate_one()
            metadata: Additional metadata

        Returns:
            Updated sample object
        """
        pass

    @abstractmethod
    def update_sample_error(
        self,
        sample: Any,
        error: Exception,
        metadata: dict[str, Any],
    ) -> Any:
        """Update sample with error information.

        Args:
            sample: Framework-specific sample object
            error: Exception that occurred
            metadata: Additional metadata

        Returns:
            Updated sample object
        """
        pass
