"""
AReaL framework adapter for OpenSage.

This adapter provides integration between OpenSage agents and the AReaL
RL framework's rollout system.

Design principle:
- AReaL passes an ADK-compatible model (ArealLlm) to OpenSage
- ArealLlm wraps ArealOpenAI which tracks token log probs and rewards
- OpenSage uses ArealLlm like any other BaseLlm model
- This is similar to how CAMEL integrates with AReaL

Architecture:
    AReaL Workflow
         │
         ├── Create ArealOpenAI client (tracks log probs, rewards)
         │
         ├── Create ArealLlm(openai_client=client)
         │
         └── Pass model to OpenSage
                  │
                  ▼
         OpenSage Evaluation
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

import json
import logging
from typing import TYPE_CHECKING, Any

from google.adk.models import BaseLlm

from .base import BaseAdapter

if TYPE_CHECKING:
    from opensage.evaluation.base import Evaluation
    from opensage.evaluation.rl_adapters.benchmark_interface import BenchmarkInterface
    from opensage.session import OpenSageSession

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
            sample (Any): Dict from AReaL dataset
        Returns:
            dict: Dict in format expected by Evaluation._create_task()
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
        """Generate response using OpenSage Evaluation with ArealLlm model.

        This method:
        1. Converts data to sample dict
        2. Creates EvaluationTask
        3. Replaces agent's model with the provided ArealLlm
        4. Runs Evaluation._generate_one
        5. Computes reward using benchmark's reward_func
        6. Returns result dict with reward

        The ArealLlm model (wrapping ArealOpenAI) automatically tracks:
        - Token log probabilities for each generation
        - Response IDs for reward assignment

        Args:
            data (dict[str, Any]): Dataset sample (dict format)
            model (BaseLlm): ADK-compatible model (ArealLlm instance)
                This model wraps ArealOpenAI for automatic tracking.
            **kwargs: Additional arguments passed to Evaluation
        Returns:
            dict[str, Any]: Result dict from Evaluation._generate_one, augmented with "reward"
        """
        data_id = data.get("id", "unknown")
        logger.debug(f"=== generate() START for data_id={data_id} ===")

        try:
            # 1. Convert AReaL data to sample dict
            sample_dict = self.convert_to_sample_dict(data)

            # 2. Create EvaluationTask
            task = self.evaluation._create_task(sample_dict)
            logger.debug(f"Task created: id={task.id}, session_id={task.session_id}")

            # 3. Set model for RL integration
            task.model = model

            # 4. Run agent using Evaluation._generate_one
            result = await self.evaluation._generate_one(task)

            # 5. Compute reward using benchmark's reward_func
            reward = await self._compute_reward(data, result, model)
            result["reward"] = reward

            # 6. Attach task metadata for trajectory logging
            result["task_data"] = {
                k: v
                for k, v in data.items()
                if k not in ("prompt",)  # exclude large prompt text
            }

            logger.debug(
                f"=== generate() DONE for data_id={data_id} reward={reward} ==="
            )
            return result

        except Exception as e:
            logger.error(f"OpenSage agent error: {e}", exc_info=True)
            logger.debug(f"generate() ERROR for data_id={data_id}: {e}")
            return {
                "error": str(e),
                "reward": 0.0,
            }

    async def _compute_reward(
        self,
        data: dict[str, Any],
        result: dict[str, Any],
        model: BaseLlm,
    ) -> float:
        """Compute reward using the benchmark's reward_func.

        Constructs a lightweight sample object from the ArealOpenAI client's
        conversation history to pass to the benchmark reward function.

        Args:
            data (dict[str, Any]): Original dataset sample
            result (dict[str, Any]): Result dict from _generate_one
            model (BaseLlm): ArealLlm model (has _client with conversation history)
        Returns:
            float: Scalar reward value (float)
        """
        try:
            if not self.benchmark.has_reward_func:
                logger.debug("No reward function available for benchmark")
                logger.warning("No reward function available for benchmark")
                return 0.0

            # Build a sample-like object for the reward function.
            # The reward_func expects attributes: .status, .response, .response_length
            client = getattr(model, "_client", None)
            response_text = ""
            status = "completed"
            n_turns = 0
            tool_calls_summary = []
            last_finish_reason = "stop"

            if client is not None:
                # ArealOpenAI._cache is an InteractionCache (OrderedDict)
                cache = getattr(client, "_cache", {})
                n_turns = len(cache)
                logger.debug(f"ArealOpenAI cache has {n_turns} interaction(s)")

                # Collect conversation content from all interactions
                for interaction_id, interaction in cache.items():
                    # output_message_list contains the assistant's output
                    output_msgs = (
                        getattr(interaction, "output_message_list", None) or []
                    )
                    for msg in output_msgs:
                        role = msg.get("role", "")
                        content = msg.get("content", "") or ""
                        tc_list = msg.get("tool_calls", [])

                        if role == "assistant":
                            if content:
                                response_text += content + "\n"
                            for tc in tc_list or []:
                                func = tc.get("function", {})
                                name = func.get("name", "?")
                                args_str = func.get("arguments", "")
                                tool_calls_summary.append(f"{name}({args_str[:200]})")
                                # If this is set_model_response, capture its args as the response
                                if name == "set_model_response":
                                    response_text = args_str

                    # Track finish reason from the completion
                    completion = getattr(interaction, "completion", None)
                    if completion and completion.choices:
                        last_finish_reason = (
                            completion.choices[0].finish_reason or "stop"
                        )

                # If the last interaction was truncated (hit max_tokens)
                if last_finish_reason == "length":
                    status = "truncated"

            # Log trajectory summary
            logger.debug(
                f"--- Trajectory Summary ---\n"
                f"  n_turns: {n_turns}\n"
                f"  tool_calls: {tool_calls_summary}\n"
                f"  last_finish_reason: {last_finish_reason}\n"
                f"  status: {status}\n"
                f"  response_len: {len(response_text)}\n"
                f"  response_preview: '{response_text[:500]}'\n"
                f"--- End Summary ---"
            )

            # Create a simple namespace to mimic a Sample
            class _RewardSample:
                pass

            sample = _RewardSample()
            sample.status = type("Status", (), {"value": status})()
            sample.response = response_text
            sample.response_length = len(response_text)
            sample.metadata = data

            # Call benchmark reward function
            reward_result = await self.benchmark.reward_func(None, sample)
            logger.debug(f"Reward function returned: {reward_result}")
            logger.info(f"Reward function returned: {reward_result}")

            if isinstance(reward_result, dict):
                return float(reward_result.get("score", 0.0))
            return float(reward_result)

        except Exception as e:
            logger.warning(f"Reward computation failed, defaulting to 0.0: {e}")
            logger.debug(f"Reward computation FAILED: {e}")
            import traceback as tb_mod

            logger.debug(tb_mod.format_exc())
            return 0.0

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
            sample (Any): Not used for AReaL
            result (dict): Result dict
            metadata (dict[str, Any]): Additional metadata
        Returns:
            Any: Result dict with metadata
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
            sample (Any): Not used for AReaL
            error (Exception): Exception that occurred
            metadata (dict[str, Any]): Additional metadata
        Returns:
            Any: Error dict
        """
        logger.error(f"ArealAdapter error: {error}, metadata: {metadata}")
        return {
            "error": str(error),
            "metadata": metadata,
            "reward": 0.0,
        }
