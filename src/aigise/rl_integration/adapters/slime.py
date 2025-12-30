"""
Slime framework adapter for AIgiSE.

This adapter provides integration between AIgiSE agents and the slime
RL framework's rollout system.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BaseAdapter

logger = logging.getLogger(__name__)


class SlimeAdapter(BaseAdapter):
    """Adapter for slime RL framework integration.

    Handles the translation between slime's Sample format and AIgiSE's
    Evaluation system.

    Usage:
        adapter = SlimeAdapter(aigise_session, evaluation, benchmark)
        sample = await adapter.generate(args, sample, sampling_params)
    """

    def convert_to_sample_dict(self, sample: Any) -> dict:
        """Convert slime Sample to dict format for Evaluation.

        Args:
            sample: Slime Sample object

        Returns:
            Dict in format expected by Evaluation._create_task()
        """
        sample_dict = {}

        # Extract prompt/input
        if hasattr(sample, "prompt"):
            prompt = sample.prompt
            if isinstance(prompt, list):
                # Chat format - extract content
                sample_dict["prompt"] = prompt
            else:
                sample_dict["prompt"] = str(prompt)

        # Extract task ID if available
        if hasattr(sample, "id"):
            sample_dict["task_id"] = sample.id
        elif hasattr(sample, "task_id"):
            sample_dict["task_id"] = sample.task_id

        # Extract metadata
        if hasattr(sample, "metadata") and sample.metadata:
            sample_dict.update(sample.metadata)

        return sample_dict

    async def generate(
        self,
        args: Any,
        sample: Any,
        sampling_params: dict[str, Any],
    ) -> Any:
        """Generate response using AIgiSE Evaluation for slime rollout.

        Args:
            args: Rollout arguments from slime
            sample: Sample object with prompt and metadata
            sampling_params: Sampling parameters

        Returns:
            Updated Sample object with response and status
        """
        try:
            # 1. Convert slime Sample to dict format
            sample_dict = self.convert_to_sample_dict(sample)

            # 2. Create EvaluationTask
            task = self.evaluation._create_task(sample_dict)

            # 3. Run agent using Evaluation._generate_sample
            result = await self.evaluation._generate_sample(task)

            # 4. Update sample with success
            metadata = {
                "aigise_session_id": task.session_id,
                "task_name": task.task_name,
            }
            return self.update_sample_success(sample, result, metadata)

        except Exception as e:
            logger.error(f"AIgiSE agent error: {e}")
            metadata = {
                "aigise_session_id": self.session_id,
                "aigise_error": str(e),
            }
            return self.update_sample_error(sample, e, metadata)

    def update_sample_success(
        self,
        sample: Any,
        result: dict,
        metadata: dict[str, Any],
    ) -> Any:
        """Update slime Sample with successful result.

        Args:
            sample: Slime Sample object
            result: Result dict from Evaluation._generate_sample()
            metadata: Additional metadata

        Returns:
            Updated Sample object
        """
        # Extract response from result (if available)
        response = ""
        if "response" in result:
            response = result["response"]
        elif "session" in result and result["session"]:
            # Try to extract from session events
            session = result["session"]
            if hasattr(session, "events"):
                for event in reversed(session.events):
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.text and not getattr(part, "thought", False):
                                response = part.text
                                break
                    if response:
                        break

        sample.response = response
        sample.response_length = len(response)

        # Set status to completed
        if hasattr(sample, "Status"):
            sample.status = sample.Status.COMPLETED
        else:
            sample.status = "completed"

        # Update metadata
        if sample.metadata is None:
            sample.metadata = {}
        sample.metadata.update(metadata)

        # Add result metadata if available
        if "metadata" in result:
            sample.metadata["aigise_result"] = result["metadata"]

        return sample

    def update_sample_error(
        self,
        sample: Any,
        error: Exception,
        metadata: dict[str, Any],
    ) -> Any:
        """Update slime Sample with error information.

        Args:
            sample: Slime Sample object
            error: Exception that occurred
            metadata: Additional metadata

        Returns:
            Updated Sample object
        """
        # Set status to aborted
        if hasattr(sample, "Status"):
            sample.status = sample.Status.ABORTED
        else:
            sample.status = "aborted"

        # Update metadata
        if sample.metadata is None:
            sample.metadata = {}
        sample.metadata.update(metadata)

        return sample
