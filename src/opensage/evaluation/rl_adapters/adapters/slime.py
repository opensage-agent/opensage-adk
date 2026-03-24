"""
Slime framework adapter for OpenSage.

This adapter integrates OpenSage agents with slime's RL training pipeline.
It follows the same pattern as ArealAdapter: inject a custom BaseLlm (SlimeLlm)
into the agent so that all LLM calls route to slime's sglang server while
tracking tokens and loss_mask for GRPO training.

Architecture:
    slime rollout loop
        └── generate_with_opensage.generate(args, sample, sampling_params)
                └── SlimeAdapter.generate(args, sample, sampling_params)
                        │
                        ├── Create SlimeLlm (BaseLlm → sglang)
                        ├── Set task.model = slime_llm
                        ├── Evaluation._generate_one(task)
                        │     └── Agent runs with SlimeLlm
                        │         ├── Each LLM call → sglang /generate
                        │         ├── Each response tracked (tokens, loss_mask)
                        │         └── Tool calls executed in sandbox normally
                        └── Collect tokens/loss_mask → fill slime Sample
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BaseAdapter

logger = logging.getLogger(__name__)


def _schema_to_openai_dict(schema) -> dict:
    """Convert an ADK Schema object to a plain dict for OpenAI tool format.

    Handles nested properties, items, enums, and type conversions.
    Mirrors ``google.adk.models.lite_llm._schema_to_dict``.
    """
    from google.genai import types as genai_types

    if isinstance(schema, dict):
        schema_dict = dict(schema)
    elif hasattr(schema, "model_dump"):
        schema_dict = schema.model_dump(exclude_none=True)
    else:
        return {"type": "string"}

    if "type" in schema_dict and schema_dict["type"] is not None:
        t = schema_dict["type"]
        schema_dict["type"] = (
            t.value if isinstance(t, genai_types.Type) else str(t)
        ).lower()

    if "items" in schema_dict and isinstance(schema_dict["items"], (dict,)):
        schema_dict["items"] = _schema_to_openai_dict(schema_dict["items"])
    elif "items" in schema_dict and hasattr(schema_dict["items"], "model_dump"):
        schema_dict["items"] = _schema_to_openai_dict(schema_dict["items"])

    if "properties" in schema_dict:
        new_props = {}
        for key, value in schema_dict["properties"].items():
            if isinstance(value, dict) or hasattr(value, "model_dump"):
                new_props[key] = _schema_to_openai_dict(value)
            else:
                new_props[key] = value
        schema_dict["properties"] = new_props

    enum_values = schema_dict.get("enum")
    if isinstance(enum_values, (list, tuple)):
        schema_dict["enum"] = [v for v in enum_values if v is not None]

    return schema_dict


class SlimeAdapter(BaseAdapter):
    """Adapter for slime RL framework integration.

    Handles the translation between slime's Sample format and OpenSage's
    Evaluation system, with token-level tracking for RL training.

    Key difference from ArealAdapter:
    - ArealAdapter receives a pre-built model from AReaL
    - SlimeAdapter *creates* the SlimeLlm itself from rollout args

    Usage:
        adapter = SlimeAdapter(opensage_session, evaluation, benchmark)
        sample = await adapter.generate(args, sample, sampling_params)
    """

    def convert_to_sample_dict(self, sample: Any) -> dict:
        """Convert slime Sample to dict format for Evaluation.

        The slime Sample's ``prompt`` field contains the task index (int)
        or raw prompt data. We also extract ``metadata`` which typically
        holds the full dataset row.

        Args:
            sample (Any): Slime Sample object
        Returns:
            dict: Dict in format expected by Evaluation._create_task()
        """
        # The sample.metadata should contain the full dataset row
        # (set by generate_with_opensage.py when building the Sample)
        if hasattr(sample, "metadata") and sample.metadata:
            sample_dict = dict(sample.metadata)
        else:
            sample_dict = {}

        # Extract prompt/input
        if hasattr(sample, "prompt"):
            prompt = sample.prompt
            if isinstance(prompt, list):
                sample_dict["prompt"] = prompt
            else:
                sample_dict["prompt"] = str(prompt)

        # Extract task ID if available
        if hasattr(sample, "id"):
            sample_dict["task_id"] = sample.id
        elif hasattr(sample, "task_id"):
            sample_dict["task_id"] = sample.task_id

        return sample_dict

    async def generate(
        self,
        args: Any,
        sample: Any,
        sampling_params: dict[str, Any],
    ) -> Any:
        """Generate response using OpenSage Evaluation for slime rollout.

        This is the main entry point called by slime's rollout system.
        It creates a SlimeLlm, injects it into the agent via the Evaluation
        system, runs the full agent trajectory, and collects token data
        into the slime Sample.

        Args:
            args (Any): Rollout arguments from slime (has sglang_router_ip, etc.)
            sample (Any): Sample object with prompt (task index) and metadata
            sampling_params (dict[str, Any]): Sampling parameters for sglang
        Returns:
            Any: Updated Sample object with tokens, loss_mask, response, reward, status
        """
        slime_llm = None
        sample_id = getattr(sample, "id", None) or getattr(sample, "task_id", "unknown")
        logger.debug(f"SlimeAdapter.generate() starting for sample={sample_id}")

        try:
            # 1. Create SlimeLlm configured for this rollout
            slime_llm = self._create_slime_llm(args, sampling_params)

            # 2. Convert slime Sample to dict format
            sample_dict = self.convert_to_sample_dict(sample)

            # 3. Create EvaluationTask
            task = self.evaluation._create_task(sample_dict)

            # 4. Inject SlimeLlm as the model (like ArealAdapter)
            task.model = slime_llm

            # 5. Run agent using Evaluation._generate_one
            result = await self.evaluation._generate_one(task)

            # 6. Build final sample with token tracking data
            result_sample = self._build_result_sample(sample, slime_llm, result, task)

            # 7. Compute reward via benchmark's reward_func (after response
            #    is populated so the reward function can inspect it).
            #    Skip reward computation for aborted samples.
            if not slime_llm._aborted:
                reward = await self._compute_reward(args, result_sample)
                result_sample.reward = reward
                logger.info(f"Reward computed: {reward}")
            else:
                result_sample.reward = 0.0
                logger.warning("Skipping reward computation for aborted sample")

            return result_sample

        except Exception as e:
            logger.error(f"OpenSage agent error: {e}", exc_info=True)
            return self._build_error_sample(sample, e, slime_llm=slime_llm)

    def _create_slime_llm(
        self,
        args: Any,
        sampling_params: dict[str, Any],
    ) -> Any:
        """Create and configure a SlimeLlm instance.

        Args:
            args (Any): Rollout args from slime (must have sglang_router_ip, sglang_router_port)
            sampling_params (dict[str, Any]): Sampling parameters for sglang
        Returns:
            Any: Configured SlimeLlm instance
        """
        import importlib

        from opensage.evaluation.rl_adapters.slime_llm import SlimeLlm

        sglang_rollout = importlib.import_module("slime.rollout.sglang_rollout")
        GenerateState = getattr(sglang_rollout, "GenerateState")

        # Get tokenizer from slime's GenerateState singleton
        state = GenerateState(args)
        tokenizer = state.tokenizer

        # Build sglang URL
        sglang_url = (
            f"http://{args.sglang_router_ip}:{args.sglang_router_port}/generate"
        )

        # Extract tools_info from the agent if possible
        tools_info = self._extract_tools_info()

        # Create and configure SlimeLlm
        slime_llm = SlimeLlm(model="slime-sglang")
        slime_llm.configure(
            sglang_url=sglang_url,
            tokenizer=tokenizer,
            sampling_params=sampling_params,
            tools_info=tools_info,
        )

        return slime_llm

    def _extract_tools_info(self) -> list[dict[str, Any]]:
        """Extract OpenAI-format tool definitions from the evaluation's agent.

        Uses ADK's ``_get_declaration()`` on each tool to get a proper
        ``FunctionDeclaration``, then converts it to OpenAI format using
        the same logic as ``lite_llm._function_declaration_to_tool_param``.
        This ensures the tokenizer's chat template receives accurate tool
        schemas (parameter names, types, descriptions).

        Handles both ADK Tool objects and plain Python functions (the latter
        are wrapped in ``FunctionTool`` first to obtain declarations).
        """
        try:
            import types as builtin_types

            from google.adk.agents import LlmAgent
            from google.adk.tools.function_tool import FunctionTool

            agent = self.evaluation._mk_agent_original(
                opensage_session_id="tools_inspection_dummy"
            )
            if not isinstance(agent, LlmAgent) or not agent.tools:
                return []

            tools_info = []
            for tool in agent.tools:
                try:
                    # Plain functions need to be wrapped in FunctionTool first
                    if isinstance(tool, builtin_types.FunctionType):
                        tool = FunctionTool(func=tool)
                    get_declaration = getattr(tool, "_get_declaration", None)
                    if not callable(get_declaration):
                        continue
                    decl: Any = get_declaration()
                    if decl is None or not decl.name:
                        continue
                    tools_info.append(self._function_declaration_to_openai(decl))
                except Exception as e:
                    logger.debug(f"Skipping tool {getattr(tool, 'name', '?')}: {e}")

            logger.info(
                f"Extracted {len(tools_info)} tool definitions for chat template"
            )
            return tools_info
        except Exception as e:
            logger.warning(f"Could not extract tools_info: {e}")
            return []

    @staticmethod
    def _function_declaration_to_openai(decl) -> dict[str, Any]:
        """Convert an ADK FunctionDeclaration to OpenAI tool format.

        Mirrors ``google.adk.models.lite_llm._function_declaration_to_tool_param``.
        """
        parameters: dict[str, Any] = {"type": "object", "properties": {}}

        if decl.parameters and decl.parameters.properties:
            properties = {}
            for key, value in decl.parameters.properties.items():
                if hasattr(value, "model_dump"):
                    properties[key] = _schema_to_openai_dict(value)
                elif isinstance(value, dict):
                    properties[key] = value
                else:
                    properties[key] = {"type": "string"}
            parameters = {"type": "object", "properties": properties}

            required_fields = getattr(decl.parameters, "required", None)
            if required_fields:
                parameters["required"] = required_fields
        elif getattr(decl, "parameters_json_schema", None):
            parameters = decl.parameters_json_schema

        return {
            "type": "function",
            "function": {
                "name": decl.name,
                "description": decl.description or "",
                "parameters": parameters,
            },
        }

    async def _compute_reward(self, args: Any, sample: Any) -> float:
        """Compute reward using the benchmark's reward_func.

        Falls back to 0.0 if no reward function is available.

        Args:
            args (Any): Rollout arguments from slime
            sample (Any): Sample object with response already populated
        Returns:
            float: Scalar reward value (float)
        """
        try:
            logger.debug(f"has_reward_func={self.benchmark.has_reward_func}")
            if self.benchmark.has_reward_func:
                reward_result = await self.benchmark.reward_func(args, sample)
                logger.info(f"Reward function returned: {reward_result}")
                if isinstance(reward_result, dict):
                    return float(reward_result.get("score", 0.0))
                return float(reward_result)
            else:
                logger.warning("No reward function available for benchmark")
        except Exception as e:
            logger.warning(f"Reward computation failed, defaulting to 0.0: {e}")
        return 0.0

    def _build_result_sample(
        self,
        sample: Any,
        slime_llm: Any,
        result: dict,
        task: Any,
    ) -> Any:
        """Build the final slime Sample with training data from SlimeLlm tracker.

        Args:
            sample (Any): Original slime Sample
            slime_llm (Any): SlimeLlm instance with populated token tracker
            result (dict): Result dict from Evaluation._generate_one
            task (Any): EvaluationTask
        Returns:
            Any: Updated Sample with tokens, loss_mask, response, status, reward
        """
        tracker = slime_llm.tracker

        # Set token tracking data.
        # response_token_ids and loss_masks should be the same length, but
        # due to tokenizer inconsistencies in _get_token_delta (chat template
        # string-diff + re-tokenize can produce slightly different token counts),
        # they may diverge.  Truncate to the shorter length so that slime's
        # assertion `len(loss_mask) == response_length` always holds.
        resp_len = min(len(tracker.response_token_ids), len(tracker.loss_masks))
        if len(tracker.response_token_ids) != len(tracker.loss_masks):
            logger.warning(
                f"Token tracking mismatch: response_token_ids={len(tracker.response_token_ids)}, "
                f"loss_masks={len(tracker.loss_masks)}.  Truncating to {resp_len}."
            )

        prompt_tokens = tracker.prompt_token_ids
        if not prompt_tokens:
            # Agent made zero LLM calls — no prompt was recorded.
            # We still need valid prompt tokens for Megatron's F.pad (prompt_length
            # must be > 0, otherwise pad=(−1, 1) → "narrow(): length must be
            # non-negative").  Use the same fallback strategy as _build_error_sample.
            logger.warning(
                "No prompt tokens recorded (agent made 0 LLM calls). "
                "Using fallback prompt tokens."
            )
            if hasattr(slime_llm, "_tokenizer") and slime_llm._tokenizer is not None:
                prompt_tokens = slime_llm._tokenizer.encode(
                    "<|im_start|>",
                    add_special_tokens=False,
                ) or [0]
            else:
                prompt_tokens = [0]

        sample.tokens = prompt_tokens + tracker.response_token_ids[:resp_len]
        sample.loss_mask = tracker.loss_masks[:resp_len]
        sample.response_length = resp_len

        # Set response (concatenated assistant messages)
        sample.response = "".join(
            msg.get("content", "")
            for msg in tracker.messages
            if msg.get("role") == "assistant" and msg.get("content")
        )

        # Set prompt (initial text sent to the model)
        if tracker.messages:
            initial_msgs = [
                m for m in tracker.messages if m.get("role") in ("system", "user")
            ][:2]
            if initial_msgs:
                prompt_text = slime_llm._tokenizer.apply_chat_template(
                    initial_msgs,
                    tokenize=False,
                    add_generation_prompt=True,
                    tools=slime_llm._tools_info if slime_llm._tools_info else None,
                )
                sample.prompt = prompt_text

        # Reward is set by generate() after _compute_reward();
        # leave sample.reward untouched here (default 0.0 from Sample).

        if slime_llm._aborted or resp_len == 0:
            sample.status = sample.Status.TRUNCATED
        else:
            sample.status = sample.Status.COMPLETED

        # Set metadata
        if sample.metadata is None:
            sample.metadata = {}
        sample.metadata.update(
            {
                "opensage_session_id": task.session_id,
                "task_name": task.id,
            }
        )

        logger.info(
            f"Built result sample: tokens={len(sample.tokens)}, "
            f"response_length={sample.response_length}, "
            f"loss_mask_sum={sum(tracker.loss_masks)}"
        )

        return sample

    def _build_error_sample(
        self,
        sample: Any,
        error: Exception,
        slime_llm: Any = None,
    ) -> Any:
        """Build a slime Sample for error cases."""
        logger.warning(f"Building error sample: {error.__class__.__name__}: {error}")
        # Always use TRUNCATED instead of ABORTED because Slime doesn't properly
        # handle ABORTED samples in reward processing. See: https://github.com/THUDM/slime/issues/200
        sample.status = sample.Status.TRUNCATED
        sample.reward = 0.0

        # Ensure prompt tokens so Megatron's F.pad doesn't crash with negative padding
        # (total_length must be > 0 when response_length=0)
        if slime_llm is not None and hasattr(slime_llm, "tracker"):
            prompt_tokens = slime_llm.tracker.prompt_token_ids
            if prompt_tokens:
                sample.tokens = prompt_tokens
        if not sample.tokens:
            # Fallback: need at least prompt tokens for Megatron. Use tokenizer if available.
            if (
                slime_llm is not None
                and hasattr(slime_llm, "_tokenizer")
                and slime_llm._tokenizer
            ):
                # Encode a minimal prompt to get valid token IDs
                sample.tokens = slime_llm._tokenizer.encode(
                    "<|im_start|>",
                    add_special_tokens=False,
                ) or [0]
            else:
                sample.tokens = [0]  # absolute fallback: single padding token

        sample.loss_mask = []
        sample.response_length = 0

        if sample.metadata is None:
            sample.metadata = {}
        sample.metadata["opensage_error"] = str(error)

        return sample

    # --- BaseAdapter interface stubs (kept for compatibility) ---

    def update_sample_success(
        self,
        sample: Any,
        result: dict,
        metadata: dict[str, Any],
    ) -> Any:
        """Not used in new architecture; see _build_result_sample."""
        return sample

    def update_sample_error(
        self,
        sample: Any,
        error: Exception,
        metadata: dict[str, Any],
    ) -> Any:
        """Not used in new architecture; see _build_error_sample."""
        return self._build_error_sample(sample, error)
