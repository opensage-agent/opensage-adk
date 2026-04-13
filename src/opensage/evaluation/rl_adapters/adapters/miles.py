"""
Miles framework adapter for OpenSage.

This adapter integrates OpenSage agents with Miles' RL training pipeline.
Unlike SlimeAdapter which tracks tokens in-process, Miles handles all token
tracking externally via its TITO (Token In Token Out) session server.

The adapter simply needs to:
1. Create a LiteLlm pointing to Miles' session server endpoint
2. Inject it into the agent
3. Run the agent and compute reward
4. Return reward + metrics (no token data needed)

Architecture:
    Miles agentic_tool_call.generate()
        └── opensage_agent_function.run(base_url, prompt, metadata, ...)
                └── MilesAdapter.generate(base_url, prompt, metadata, sampling_params)
                        │
                        ├── Create LiteLlm(base_url=miles_session_server)
                        ├── Set task.model = litellm
                        ├── Evaluation._generate_one(task)
                        │     └── Agent runs with LiteLlm
                        │         ├── Each LLM call → Miles session server → sglang
                        │         ├── Miles records tokens automatically (TITO)
                        │         └── Tool calls executed in sandbox normally
                        └── Compute reward → return {reward, exit_status, ...}
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from .base import BaseAdapter

logger = logging.getLogger(__name__)


class MilesAdapter(BaseAdapter):
    """Adapter for Miles RL framework integration.

    Key difference from SlimeAdapter:
    - SlimeAdapter tracks tokens in-process via SlimeLlm/TokenTracker
    - MilesAdapter does NOT track tokens — Miles' TITO session server
      handles all token recording externally
    - Result is a plain dict (not a framework-specific Sample object)
    """

    def convert_to_sample_dict(self, sample: Any) -> dict:
        """Convert Miles metadata + prompt to dict for Evaluation._create_task().

        In Miles integration, `sample` is already a dict with prompt and metadata.
        """
        if isinstance(sample, dict):
            return dict(sample)
        return {"prompt": str(sample)}

    async def generate(
        self,
        args: Any = None,
        sample: Any = None,
        sampling_params: dict[str, Any] | None = None,
        *,
        base_url: str = "",
        prompt: Any = "",
        metadata: dict[str, Any] | None = None,
        model_name: str = "",
    ) -> dict[str, Any]:
        """Run OpenSage agent for Miles rollout.

        Called by opensage_agent_function.run() on the Miles side.

        Args:
            args: Unused (kept for BaseAdapter interface compatibility)
            sample: Unused (kept for BaseAdapter interface compatibility)
            sampling_params: Sampling parameters forwarded to LiteLlm
            base_url: Miles session server endpoint
                      (e.g. http://host:30000/sessions/{session_id})
            prompt: Task prompt from Miles sample
            metadata: Task metadata from Miles sample
            model_name: Model name for the agent

        Returns:
            dict with {reward, exit_status, agent_metrics, eval_report}
        """
        metadata = metadata or {}
        sampling_params = sampling_params or {}
        t_start = time.monotonic()

        try:
            # 1. Create LiteLlm pointing to Miles session server
            litellm_model = self._create_litellm(
                base_url=base_url,
                model_name=model_name,
                sampling_params=sampling_params,
            )

            # 2. Build sample dict for Evaluation._create_task()
            sample_dict = {
                "prompt": prompt,
                **metadata,
            }
            task = self.evaluation._create_task(sample_dict)

            # 3. Inject LiteLlm as the model
            task.model = litellm_model

            # 4. Run agent
            result = await self.evaluation._generate_one(task)

            # 5. Compute reward
            reward = await self._compute_reward(result)

            # 6. Build metrics
            agent_run_time = time.monotonic() - t_start
            agent_metrics = self._extract_agent_metrics(result, agent_run_time)

            logger.info(
                f"Miles generate done: reward={reward}, "
                f"time={agent_run_time:.1f}s, "
                f"task={task.id}"
            )

            return {
                "reward": reward,
                "exit_status": "Submitted",
                "agent_metrics": agent_metrics,
                "eval_report": result if isinstance(result, dict) else {},
            }

        except Exception as e:
            logger.exception(f"OpenSage agent error: {e}")
            return {
                "reward": 0.0,
                "exit_status": f"Error: {type(e).__name__}",
                "agent_metrics": {
                    "agent_run_time": time.monotonic() - t_start,
                },
                "eval_report": {"error": str(e)},
            }

    def _create_litellm(
        self,
        base_url: str,
        model_name: str,
        sampling_params: dict[str, Any],
    ) -> Any:
        """Create a LiteLlm instance pointing to Miles session server.

        Args:
            base_url: Miles session server endpoint (includes /sessions/{id})
            model_name: Model name string
            sampling_params: Sampling parameters
        Returns:
            LiteLlm instance configured for Miles
        """
        from google.adk.models.lite_llm import LiteLlm

        # Miles session server exposes OpenAI-compatible /v1/chat/completions
        # base_url from Miles is like: http://host:30000/sessions/{session_id}
        # LiteLlm needs the /v1 suffix for OpenAI-compatible routing
        api_base = f"{base_url}/v1" if not base_url.endswith("/v1") else base_url

        model_str = model_name or os.getenv("AGENT_MODEL_NAME", "model")
        # Prefix with openai/ for LiteLlm routing
        if not model_str.startswith("openai/"):
            model_str = f"openai/{model_str}"

        litellm_kwargs = {
            "api_key": os.getenv("OPENAI_API_KEY", "dummy"),
            "base_url": api_base,
        }

        # Forward relevant sampling params
        for key in ("temperature", "top_p", "max_tokens"):
            if key in sampling_params:
                litellm_kwargs[key] = sampling_params[key]

        model = LiteLlm(model=model_str, **litellm_kwargs)

        logger.info(f"Created LiteLlm: model={model_str}, base_url={api_base}")
        return model

    async def _compute_reward(self, result: Any) -> float:
        """Compute reward using the benchmark's reward_func.

        Args:
            result: Result from Evaluation._generate_one()
        Returns:
            float: Scalar reward value
        """
        try:
            if self.benchmark.has_reward_func:
                reward_result = await self.benchmark.reward_func(None, result)
                if isinstance(reward_result, dict):
                    return float(reward_result.get("score", 0.0))
                return float(reward_result)
        except Exception as e:
            logger.warning(f"Reward computation failed: {e}")
        return 0.0

    def _extract_agent_metrics(
        self,
        result: Any,
        agent_run_time: float,
    ) -> dict[str, Any]:
        """Extract agent metrics from evaluation result.

        Args:
            result: Result from Evaluation._generate_one()
            agent_run_time: Total wall-clock time
        Returns:
            dict with agent timing and count metrics
        """
        metrics: dict[str, Any] = {
            "agent_run_time": agent_run_time,
        }
        if isinstance(result, dict):
            for key in (
                "turns",
                "tool_calls",
                "model_query_time_sum",
                "env_execution_time_sum",
            ):
                if key in result:
                    metrics[key] = result[key]
        return metrics

    def update_sample_success(
        self,
        sample: Any,
        result: dict,
        metadata: dict[str, Any],
    ) -> Any:
        """Not used — Miles handles sample building via TITO."""
        return sample

    def update_sample_error(
        self,
        sample: Any,
        error: Exception,
        metadata: dict[str, Any],
    ) -> Any:
        """Not used — errors are returned as dicts."""
        return sample
