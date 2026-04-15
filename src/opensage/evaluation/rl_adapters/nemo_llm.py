"""
NemoLlm — BaseLlm that calls NemoGym's model server /v1/responses directly.

Bypasses litellm entirely. Calls the model server's /v1/responses endpoint
via aiohttp, which returns prompt_token_ids, generation_token_ids, and
generation_log_probs for RL training.

This matches how NemoGym's simple_agent calls the model server — same
endpoint, same format, same logprob data.

Usage:
    model = NemoLlm(model="qwen3.5", base_url="http://model-server:port")
    task.model = model
    await evaluation._generate_one(task)
    logprob_turns = model.get_logprob_turns()
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncGenerator, List, Optional
from uuid import uuid4

import aiohttp
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import ConfigDict

logger = logging.getLogger(__name__)


@dataclass
class LogprobTurn:
    """Logprob data for a single LLM generation turn."""
    prompt_token_ids: List[int]
    generation_token_ids: List[int]
    generation_log_probs: List[float]


class NemoLlm(BaseLlm):
    """BaseLlm that calls NemoGym model server /v1/responses directly.

    Multi-turn token contiguity: vLLM re-tokenizes input messages each call,
    which can produce different tokens than what we generated in the prior turn
    (whitespace handling, special tokens, etc.). To preserve token contiguity
    for RL training, we attach the prior turn's prompt_token_ids,
    generation_token_ids, and generation_log_probs to the matching assistant
    item on each subsequent request (mirrors NemoGym's harbor_agent behavior).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    model: str = "nemo"

    _base_url: str = ""
    _api_key: str = "dummy"
    _logprob_turns: list = []
    # Token IDs from the most recent turn (used for on-policy correction)
    _last_prompt_token_ids: Optional[List[int]] = None
    _last_generation_token_ids: Optional[List[int]] = None
    _last_generation_log_probs: Optional[List[float]] = None
    # Pre-check: if the running prompt+gen total reaches this, signal the
    # agent to finish so it stops before vLLM rejects over-context requests.
    _max_prompt_tokens: Optional[int] = None
    # Tracks whether we've already signaled finish_task once. Two-step exit:
    #   1) first trigger → yield finish_task function_call (sets
    #      state.task_finished=True via the tool's side effect)
    #   2) subsequent triggers → yield text-only STOP (ends ADK's inner loop;
    #      HarborEvaluation's outer loop sees task_finished=True and exits)
    _finish_task_signaled: bool = False

    def __init__(
        self,
        *,
        model: str = "nemo",
        base_url: str = "",
        api_key: str = "dummy",
        max_prompt_tokens: Optional[int] = None,
        **kwargs,
    ):
        super().__init__(model=model, **kwargs)
        self._base_url = base_url
        self._api_key = api_key
        self._logprob_turns = []
        self._last_prompt_token_ids = None
        self._last_generation_token_ids = None
        self._last_generation_log_probs = None
        self._max_prompt_tokens = max_prompt_tokens
        self._finish_task_signaled = False

    def get_logprob_turns(self) -> List[LogprobTurn]:
        return list(self._logprob_turns)

    def reset_logprobs(self):
        self._logprob_turns = []
        self._last_prompt_token_ids = None
        self._last_generation_token_ids = None
        self._last_generation_log_probs = None

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        """Call model server /v1/responses and return ADK LlmResponse."""
        self._maybe_append_user_content(llm_request)

        # Pre-check: if the cumulative prompt+gen from the prior turn already
        # exceeds the budget, the next request would be even larger and vLLM
        # would reject it (then the agent + NemoGym retry loops spin until
        # max_llm_calls). Yield a synthetic finish_task so the agent gracefully
        # ends with the partial trajectory we have so far (which preserves
        # contiguity for NeMo RL training).
        if (
            self._max_prompt_tokens is not None
            and self._last_prompt_token_ids is not None
        ):
            cumulative = (
                len(self._last_prompt_token_ids)
                + len(self._last_generation_token_ids or [])
            )
            if cumulative >= self._max_prompt_tokens:
                tool_names = (
                    set((llm_request.tools_dict or {}).keys())
                    if llm_request.tools_dict
                    else set()
                )
                # Two-step exit for HarborEvaluation's run_until_explicit_finish
                # loop. Step 1: yield a finish_task call the first time we
                # trip — the tool's side effect sets state.task_finished=True.
                # Step 2: any subsequent trip yields text-only so ADK's inner
                # loop exits, the outer loop reads task_finished, and the
                # whole rollout ends.
                if not self._finish_task_signaled and "finish_task" in tool_names:
                    logger.warning(
                        f"NemoLlm context-limit pre-check tripped: cumulative tokens "
                        f"{cumulative} >= max_prompt_tokens {self._max_prompt_tokens}. "
                        "Yielding finish_task to set state.task_finished=True."
                    )
                    self._finish_task_signaled = True
                    parts = [types.Part(function_call=types.FunctionCall(
                        id=f"call_{uuid4().hex[:8]}",
                        name="finish_task",
                        args={},
                    ))]
                else:
                    logger.warning(
                        f"NemoLlm context-limit pre-check tripped again: "
                        f"{cumulative} >= {self._max_prompt_tokens}. "
                        "Yielding text-only STOP so outer loop sees task_finished."
                    )
                    parts = [types.Part(
                        text="[NemoLlm context-limit pre-check: ending task]"
                    )]
                yield LlmResponse(
                    content=types.Content(role="model", parts=parts),
                    partial=False,
                    finish_reason=types.FinishReason.STOP,
                )
                return

        # Convert LlmRequest → Responses API format
        input_items, tools = _llm_request_to_responses_input(llm_request)

        # Attach token IDs from the previous turn to the last assistant-related
        # input item (assistant message or function_call). vLLM's responses API
        # uses these to skip re-tokenization, preserving on-policy token
        # contiguity that NeMo RL's trajectory collector requires.
        if self._last_prompt_token_ids is not None:
            for item in reversed(input_items):
                is_assistant_message = (
                    item.get("type") in (None, "message")
                    and item.get("role") in ("assistant", "model")
                )
                is_function_call = item.get("type") == "function_call"
                if is_assistant_message or is_function_call:
                    item["prompt_token_ids"] = self._last_prompt_token_ids
                    item["generation_token_ids"] = self._last_generation_token_ids or []
                    item["generation_log_probs"] = self._last_generation_log_probs or []
                    break

        body: dict[str, Any] = {
            "input": input_items,
            "model": self.model,
            # Always include temperature and top_p — NeMo RL's vLLM worker
            # asserts these match the training config exactly
            "temperature": 1.0,
            "top_p": 1.0,
        }
        if tools:
            body["tools"] = tools

        # Forward generation params (override defaults if set)
        if llm_request.config:
            if llm_request.config.temperature is not None:
                body["temperature"] = llm_request.config.temperature
            if llm_request.config.top_p is not None:
                body["top_p"] = llm_request.config.top_p
            if llm_request.config.max_output_tokens is not None:
                body["max_output_tokens"] = llm_request.config.max_output_tokens
            if getattr(llm_request.config, "top_k", None) is not None:
                body["top_k"] = llm_request.config.top_k

        # POST to model server /v1/responses
        url = f"{self._base_url}/v1/responses"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        async with aiohttp.ClientSession() as session:
            # Long timeout: RL rollouts with high concurrency can queue for a while in vLLM
            async with session.post(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=1800)) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"Model server error {resp.status}: {error_text[:1000]}")
                    yield LlmResponse(
                        content=types.Content(role="model", parts=[types.Part(text=f"[Error: {resp.status}]")]),
                        partial=False,
                        finish_reason=types.FinishReason.STOP,
                        error_message=f"Model server returned {resp.status}",
                    )
                    return

                response_data = await resp.json()

        # Extract logprob data — only from the LAST output item that has it
        # (model server attaches logprobs to the last training-capable output item)
        output_items = response_data.get("output", [])
        for item in reversed(output_items):
            if not isinstance(item, dict):
                continue
            if "generation_token_ids" not in item:
                continue

            gen_ids = item["generation_token_ids"]
            if gen_ids and isinstance(gen_ids[0], str):
                gen_ids = [int(tid.removeprefix("token_id:")) for tid in gen_ids]

            prompt_ids = item.get("prompt_token_ids", []) or []
            log_probs = item.get("generation_log_probs", []) or []

            self._logprob_turns.append(LogprobTurn(
                prompt_token_ids=prompt_ids,
                generation_token_ids=gen_ids,
                generation_log_probs=log_probs,
            ))
            # Cache for next turn's on-policy correction
            self._last_prompt_token_ids = prompt_ids
            self._last_generation_token_ids = gen_ids
            self._last_generation_log_probs = log_probs
            break  # Only extract from the last item

        # Convert response to ADK LlmResponse
        parts = _response_output_to_parts(output_items)

        # Extract usage
        usage_metadata = None
        usage = response_data.get("usage")
        if usage:
            usage_metadata = types.GenerateContentResponseUsageMetadata(
                prompt_token_count=usage.get("input_tokens", 0),
                candidates_token_count=usage.get("output_tokens", 0),
                total_token_count=usage.get("total_tokens", 0),
            )

        # Check for incomplete (max tokens)
        finish_reason = types.FinishReason.STOP
        incomplete = response_data.get("incomplete_details")
        if incomplete and incomplete.get("reason") == "max_output_tokens":
            finish_reason = types.FinishReason.MAX_TOKENS

        yield LlmResponse(
            content=types.Content(role="model", parts=parts),
            partial=False,
            finish_reason=finish_reason,
            usage_metadata=usage_metadata,
        )


def _llm_request_to_responses_input(
    llm_request: LlmRequest,
) -> tuple[list[dict], list[dict] | None]:
    """Convert ADK LlmRequest to Responses API input + tools."""
    input_items = []

    # System instruction → developer role
    if llm_request.config and llm_request.config.system_instruction:
        input_items.append({
            "role": "developer",
            "content": llm_request.config.system_instruction,
        })

    # Content history
    for content in llm_request.contents or []:
        if not content.parts:
            continue

        role = content.role or "user"

        # Function responses → function_call_output
        func_responses = [p for p in content.parts if p.function_response]
        if func_responses:
            for p in func_responses:
                fr = p.function_response
                response_data = fr.response
                output = json.dumps(response_data) if isinstance(response_data, dict) else str(response_data)
                input_items.append({
                    "type": "function_call_output",
                    "call_id": fr.id or f"call_{uuid4().hex[:8]}",
                    "output": output,
                })
            continue

        # Separate text and function calls
        func_calls = [p for p in content.parts if p.function_call]
        text_parts = [p.text for p in content.parts if p.text and not getattr(p, "thought", False)]

        if role in ("model", "assistant"):
            # Assistant text content
            if text_parts:
                input_items.append({
                    "role": "assistant",
                    "content": "\n".join(text_parts),
                })
            # Function calls
            for p in func_calls:
                fc = p.function_call
                input_items.append({
                    "type": "function_call",
                    "call_id": fc.id or f"call_{uuid4().hex[:8]}",
                    "name": fc.name,
                    "arguments": json.dumps(fc.args or {}),
                })
        elif role == "system":
            # System messages → developer
            text = "\n".join(text_parts) if text_parts else ""
            if text:
                input_items.append({"role": "developer", "content": text})
        else:
            # User messages
            text = "\n".join(text_parts) if text_parts else ""
            if text:
                input_items.append({"role": "user", "content": text})

    # Tools
    tools = None
    if llm_request.tools_dict:
        tools = []
        for name, tool in llm_request.tools_dict.items():
            tool_schema: dict[str, Any] = {
                "type": "function",
                "name": name,
                "description": "",
                "parameters": {"type": "object", "properties": {}},
                "strict": True,
            }
            # Extract schema from tool if available
            if hasattr(tool, "_get_declaration"):
                decl = tool._get_declaration()
                if decl:
                    tool_schema["description"] = getattr(decl, "description", "") or ""
                    params = getattr(decl, "parameters", None)
                    if params:
                        tool_schema["parameters"] = (
                            params.model_dump() if hasattr(params, "model_dump") else dict(params)
                        )
            tools.append(tool_schema)

    return input_items, tools


def _response_output_to_parts(output_items: list[dict]) -> list[types.Part]:
    """Convert Responses API output items to ADK Parts."""
    parts = []

    for item in output_items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type", "")

        if item_type == "message":
            for content_item in item.get("content", []):
                if isinstance(content_item, dict):
                    text = content_item.get("text", "")
                    if text:
                        parts.append(types.Part(text=text))

        elif item_type == "function_call":
            name = item.get("name", "")
            arguments = item.get("arguments", "{}")
            call_id = item.get("call_id", "")
            try:
                args = json.loads(arguments) if isinstance(arguments, str) else arguments
            except json.JSONDecodeError:
                args = {"_raw": arguments}

            parts.append(types.Part(
                function_call=types.FunctionCall(
                    id=call_id or f"call_{uuid4().hex[:8]}",
                    name=name,
                    args=args,
                )
            ))

        elif item_type == "reasoning":
            # Reasoning items — extract summary text as thought
            for summary_item in item.get("summary", []):
                if isinstance(summary_item, dict):
                    text = summary_item.get("text", "")
                    if text:
                        parts.append(types.Part(text=text, thought=True))

    if not parts:
        parts.append(types.Part(text=""))

    return parts
