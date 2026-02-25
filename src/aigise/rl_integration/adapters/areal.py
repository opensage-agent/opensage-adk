"""
AReaL framework adapter for AIgiSE.

This adapter provides integration between AIgiSE agents and the AReaL
RL framework's rollout system.

Design principle:
- AReaL passes an ADK-compatible model (ArealLlm) to AIgiSE
- ArealLlm wraps ArealOpenAI which tracks token log probs and rewards
- AIgiSE uses ArealLlm like any other BaseLlm model
- This is similar to how CAMEL integrates with AReaL

Architecture:
    AReaL Workflow
         │
         ├── Create ArealOpenAI client (tracks log probs, rewards)
         │
         ├── Create ArealLlm(openai_client=client)
         │
         └── Pass model to AIgiSE
                  │
                  ▼
         AIgiSE Evaluation
                  │
                  ├── Replace agent's model with ArealLlm
                  │
                  └── Run agent normally
                           │
                           ▼
                  ArealLlm.generate_content_async()
                           │
                           └── ArealOpenAI.chat.completions.create()
                                    (auto-tracks log probs)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from google.adk.models import BaseLlm

from .base import BaseAdapter

if TYPE_CHECKING:
    from aigise.evaluations.base import Evaluation
    from aigise.rl_integration.benchmark_interface import BenchmarkInterface
    from aigise.session import AigiseSession

logger = logging.getLogger(__name__)


class ArealAdapter(BaseAdapter):
    """Adapter for AReaL RL framework integration.

    This adapter accepts an ADK-compatible model (ArealLlm) from AReaL,
    which wraps ArealOpenAI for automatic token log probability tracking
    and reward management.

    Usage (from AReaL side):
        from areal.experimental.adk import ArealLlm
        from areal.experimental.openai import ArealOpenAI

        # Create client and model
        client = ArealOpenAI(engine=engine, tokenizer=tokenizer, ...)
        model = ArealLlm(openai_client=client)

        # Pass model to adapter
        result = await adapter.generate(data=data, model=model)

        # After agent run, set reward and export
        client.set_last_reward(result.get("reward", 0.0))
        client.apply_reward_discount(turn_discount=0.9)
        interactions = client.export_interactions(style="individual")
    """

    def convert_to_sample_dict(self, sample: Any) -> dict:
        """Convert AReaL data dict to format for Evaluation.

        For AReaL, the sample is already a dict, so we just pass it through
        with any necessary transformations.

        Args:
            sample: Dict from AReaL dataset

        Returns:
            Dict in format expected by Evaluation._create_task()
        """
        # AReaL data is already a dict
        if isinstance(sample, dict):
            return sample.copy()

        # Fallback for other formats
        sample_dict = {}
        if hasattr(sample, "prompt"):
            sample_dict["prompt"] = sample.prompt
        if hasattr(sample, "messages"):
            sample_dict["messages"] = sample.messages
        if hasattr(sample, "id"):
            sample_dict["task_id"] = sample.id
        if hasattr(sample, "metadata"):
            sample_dict.update(sample.metadata)

        return sample_dict

    async def generate(
        self,
        data: dict[str, Any],
        model: BaseLlm,
        **kwargs,
    ) -> dict[str, Any]:
        """Generate response using AIgiSE Evaluation with ArealLlm model.

        This method:
        1. Converts data to sample dict
        2. Creates EvaluationTask
        3. Replaces agent's model with the provided ArealLlm
        4. Runs Evaluation._generate_sample
        5. Returns result dict

        The ArealLlm model (wrapping ArealOpenAI) automatically tracks:
        - Token log probabilities for each generation
        - Response IDs for reward assignment

        Args:
            data: Dataset sample (dict format)
            model: ADK-compatible model (ArealLlm instance)
                This model wraps ArealOpenAI for automatic tracking.
            **kwargs: Additional arguments passed to Evaluation

        Returns:
            Result dict from Evaluation._generate_sample
        """
        try:
            # 1. Convert AReaL data to sample dict
            sample_dict = self.convert_to_sample_dict(data)

            # 2. Create EvaluationTask
            task = self.evaluation._create_task(sample_dict)

            # 3. Set model for RL integration
            # model is a BaseLlm instance (e.g., ArealLlm wrapping ArealOpenAI)
            task.model = model

            # 4. Run agent using Evaluation._generate_sample
            result = await self.evaluation._generate_sample(task)

            return result

        except Exception as e:
            logger.error(f"AIgiSE agent error: {e}")
            return {
                "error": str(e),
                "reward": 0.0,
            }

    def update_sample_success(
        self,
        sample: Any,
        result: dict,
        metadata: dict[str, Any],
    ) -> Any:
        """Update sample with successful result.

        For AReaL, this is not typically used since we return result dict directly.
        Kept for interface compatibility.

        Args:
            sample: Not used for AReaL
            result: Result dict
            metadata: Additional metadata

        Returns:
            Result dict with metadata
        """
        if isinstance(result, dict):
            result["metadata"] = metadata
        return result

    def update_sample_error(
        self,
        sample: Any,
        error: Exception,
        metadata: dict[str, Any],
    ) -> Any:
        """Update sample with error information.

        For AReaL, we return an error dict.

        Args:
            sample: Not used for AReaL
            error: Exception that occurred
            metadata: Additional metadata

        Returns:
            Error dict
        """
        logger.error(f"ArealAdapter error: {error}, metadata: {metadata}")
        return {
            "error": str(error),
            "metadata": metadata,
            "reward": 0.0,
        }
