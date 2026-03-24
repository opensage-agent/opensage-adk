"""
SlimeLlm — A BaseLlm implementation that routes LLM calls to a slime-managed
sglang server and tracks tokens + loss_mask for RL training.

This is the OpenSage-side counterpart to tau-bench's TrainableAgentMixin.
Instead of wrapping a tau-bench Agent, we implement BaseLlm so that *any*
ADK agent can be trained via slime simply by swapping its model.

Architecture:
    ADK Agent (tool-calling loop)
        │
        └── agent.model.generate_content_async(llm_request)
                    │
                    ▼
            SlimeLlm.generate_content_async()
                    │
                    ├── Convert LlmRequest → OpenAI messages + tools
                    ├── Detect environment messages added since last call
                    ├── Track environment tokens (loss_mask=0)
                    ├── Apply chat template via tokenizer
                    ├── HTTP POST to sglang /generate
                    ├── Parse response → LlmResponse (text + function_calls)
                    ├── Track assistant tokens (loss_mask=1)
                    └── Yield LlmResponse back to agent

Token tracking strategy (mirrors tau-bench _get_token_delta):
    - prompt tokens (system + first user message): recorded once as initial prompt
    - each assistant response: loss_mask = 1  (trainable)
    - each tool/environment response: loss_mask = 0  (not trainable)
    - environment messages are detected by diffing the LlmRequest contents
      against the previously tracked messages — the ADK runner inserts tool
      results between calls to generate_content_async().
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import ConfigDict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token Tracker — accumulates tokens and loss masks across the entire rollout
# ---------------------------------------------------------------------------


@dataclass
class TokenTracker:
    """Accumulates tokenized data across multi-turn agent interactions.

    After the rollout, ``prompt_token_ids + response_token_ids`` gives the full
    sequence, and ``loss_masks`` indicates which response tokens to train on.
    """

    prompt_token_ids: list[int] = field(default_factory=list)
    response_token_ids: list[int] = field(default_factory=list)
    loss_masks: list[int] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    _prompt_set: bool = False

    @property
    def all_token_ids(self) -> list[int]:
        return self.prompt_token_ids + self.response_token_ids

    @property
    def response_length(self) -> int:
        return len(self.loss_masks)

    def set_initial_prompt(self, token_ids: list[int]) -> None:
        """Record the initial prompt tokens (called once, before first LLM call)."""
        if not self._prompt_set:
            self.prompt_token_ids = list(token_ids)
            self._prompt_set = True

    def add_assistant_tokens(self, token_ids: list[int]) -> None:
        """Record assistant response tokens (trainable, loss_mask=1)."""
        self.response_token_ids.extend(token_ids)
        self.loss_masks.extend([1] * len(token_ids))

    def add_environment_tokens(self, token_ids: list[int]) -> None:
        """Record environment/tool tokens (not trainable, loss_mask=0)."""
        self.response_token_ids.extend(token_ids)
        self.loss_masks.extend([0] * len(token_ids))


# ---------------------------------------------------------------------------
# SlimeLlm — BaseLlm that talks to sglang and tracks tokens
# ---------------------------------------------------------------------------


class SlimeLlm(BaseLlm):
    """ADK-compatible LLM that routes to a slime-managed sglang server.

    Implements ``generate_content_async`` to:
    1. Convert ADK LlmRequest to sglang-compatible text + tools format
    2. POST to sglang /generate endpoint
    3. Parse response into ADK LlmResponse (text + function_calls)
    4. Track all tokens and loss_masks for RL training

    Args:
        model: Model identifier string (for compatibility; the actual model
            is determined by what sglang is serving).
        sglang_url: Full URL to sglang generate endpoint
            (e.g. "http://127.0.0.1:30000/generate").
        tokenizer: HuggingFace tokenizer for the model being served.
        sampling_params: Sampling parameters passed to sglang.
        tools_info: OpenAI-format tool definitions for the model's chat template.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model: str = "slime-sglang"

    _sglang_url: str = ""
    _tokenizer: Any = None
    _sampling_params: dict[str, Any] = {}
    _tools_info: list[dict[str, Any]] = []
    _tracker: TokenTracker | None = None
    _conversation_messages: list[dict[str, Any]] = []
    _call_count: int = 0
    _aborted: bool = False

    def configure(
        self,
        sglang_url: str,
        tokenizer: Any,
        sampling_params: dict[str, Any],
        tools_info: list[dict[str, Any]] | None = None,
    ) -> None:
        self._sglang_url = sglang_url
        self._tokenizer = tokenizer
        self._sampling_params = sampling_params
        self._tools_info = tools_info or []
        self._tracker = TokenTracker()
        self._conversation_messages = []
        self._call_count = 0
        self._aborted = False

    @property
    def tracker(self) -> TokenTracker:
        if self._tracker is None:
            self._tracker = TokenTracker()
        return self._tracker

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        import httpx

        self._call_count += 1

        # If already aborted from a previous call, immediately return STOP.
        if self._aborted:
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text="")]),
                partial=False,
                finish_reason=types.FinishReason.STOP,
            )
            return

        current_messages = self._llm_request_to_messages(llm_request)
        logger.debug(
            f"SlimeLlm call #{self._call_count}: "
            f"{len(current_messages)} messages -> {self._sglang_url}"
        )

        # First call: record initial prompt tokens
        if not self.tracker._prompt_set:
            prompt_text = self._tokenizer.apply_chat_template(
                current_messages,
                tokenize=False,
                add_generation_prompt=True,
                tools=self._tools_info if self._tools_info else None,
            )
            prompt_token_ids = self._tokenizer(prompt_text, add_special_tokens=False)[
                "input_ids"
            ]
            self.tracker.set_initial_prompt(prompt_token_ids)
            self.tracker.messages = list(current_messages)
        else:
            # Subsequent calls: detect environment messages (tool responses, user
            # messages) that ADK inserted between our calls, and track them with
            # loss_mask=0.
            self._track_environment_messages(current_messages)

        # Apply chat template for sglang
        text_input = self._tokenizer.apply_chat_template(
            current_messages,
            tokenize=False,
            add_generation_prompt=True,
            tools=self._tools_info if self._tools_info else None,
        )

        payload = {
            "text": text_input,
            "sampling_params": self._sampling_params,
        }

        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(self._sglang_url, json=payload)
            resp.raise_for_status()
            output = resp.json()

        meta_info = output.get("meta_info", {})
        finish_reason = meta_info.get("finish_reason", {})
        logger.debug(
            f"SlimeLlm call #{self._call_count} response: "
            f"{len(output.get('text', ''))} chars, "
            f"finish_reason={finish_reason}"
        )
        if isinstance(finish_reason, dict) and finish_reason.get("type") == "abort":
            logger.warning(
                "sglang returned abort — marking as aborted, preserving accumulated tokens"
            )
            self._aborted = True
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="[ABORTED]")],
                ),
                partial=False,
                finish_reason=types.FinishReason.STOP,
            )
            return

        response_text = output.get("text", "")
        for eos in ("<|im_end|>", "<|eot_id|>"):
            if response_text.endswith(eos):
                response_text = response_text[: -len(eos)]

        parts = self._parse_response_to_parts(response_text)

        # Track assistant response tokens (loss_mask=1)
        self.tracker.messages.append({"role": "assistant", "content": response_text})
        assistant_token_ids, assistant_loss_mask = self._get_token_delta(
            self.tracker.messages, last_role="assistant"
        )
        self.tracker.response_token_ids.extend(assistant_token_ids)
        self.tracker.loss_masks.extend(assistant_loss_mask)

        llm_response = LlmResponse(
            content=types.Content(role="model", parts=parts),
            partial=False,
        )

        if isinstance(finish_reason, dict):
            fr_type = finish_reason.get("type", "stop")
            if fr_type == "length":
                llm_response.finish_reason = types.FinishReason.MAX_TOKENS
            else:
                llm_response.finish_reason = types.FinishReason.STOP
        else:
            llm_response.finish_reason = types.FinishReason.STOP

        prompt_tokens = meta_info.get("prompt_tokens", 0)
        completion_tokens = meta_info.get("completion_tokens", 0)
        if prompt_tokens or completion_tokens:
            llm_response.usage_metadata = types.GenerateContentResponseUsageMetadata(
                prompt_token_count=prompt_tokens,
                candidates_token_count=completion_tokens,
                total_token_count=prompt_tokens + completion_tokens,
            )

        yield llm_response

    def _track_environment_messages(
        self, current_messages: list[dict[str, Any]]
    ) -> None:
        """Detect and track environment messages added between LLM calls.

        The ADK runner adds tool result messages and user messages between
        calls to generate_content_async(). We detect these by comparing the
        current request's message count against our tracked message count.
        Each new non-assistant message gets tokenized with loss_mask=0.
        """
        tracked_count = len(self.tracker.messages)
        new_count = len(current_messages)

        if new_count <= tracked_count:
            return

        new_env_messages = current_messages[tracked_count:]

        for msg in new_env_messages:
            role = msg.get("role", "")
            # Skip assistant messages — we already tracked those immediately
            # after the sglang response in generate_content_async().
            if role == "assistant":
                continue

            self.tracker.messages.append(msg)
            env_token_ids, env_loss_mask = self._get_token_delta(
                self.tracker.messages, last_role=role
            )
            self.tracker.response_token_ids.extend(env_token_ids)
            self.tracker.loss_masks.extend(env_loss_mask)

            logger.debug(
                f"Tracked environment message: role={role}, tokens={len(env_token_ids)}"
            )

    def _llm_request_to_messages(self, llm_request: LlmRequest) -> list[dict[str, Any]]:
        """Convert ADK LlmRequest to OpenAI chat-completion messages."""
        messages = []

        # System instruction
        if llm_request.config and llm_request.config.system_instruction:
            messages.append(
                {
                    "role": "system",
                    "content": llm_request.config.system_instruction,
                }
            )

        # Content history
        for content in llm_request.contents or []:
            msg = self._content_to_message(content)
            if isinstance(msg, list):
                messages.extend(msg)
            elif msg:
                messages.append(msg)

        return messages

    def _content_to_message(self, content: types.Content) -> dict | list[dict] | None:
        """Convert a single ADK Content to OpenAI message(s)."""
        if not content.parts:
            return None

        # Check for function responses (tool results)
        tool_messages = []
        for part in content.parts:
            if part.function_response:
                response_data = part.function_response.response
                if isinstance(response_data, str):
                    response_content = response_data
                else:
                    try:
                        response_content = json.dumps(response_data, ensure_ascii=False)
                    except (TypeError, ValueError):
                        response_content = str(response_data)
                tool_messages.append(
                    {
                        "role": "tool",
                        "name": part.function_response.name,
                        "content": response_content,
                        "tool_call_id": part.function_response.id or "",
                    }
                )
        if tool_messages:
            return tool_messages

        # Regular user/assistant message
        role = "user" if content.role == "user" else "assistant"
        text_parts = []
        function_calls = []

        for part in content.parts:
            if part.function_call:
                function_calls.append(part.function_call)
            elif part.text:
                if not getattr(part, "thought", False):
                    text_parts.append(part.text)

        msg: dict[str, Any] = {"role": role}

        if text_parts:
            msg["content"] = "\n".join(text_parts)
        else:
            msg["content"] = None

        if function_calls and role == "assistant":
            msg["tool_calls"] = [
                {
                    "id": fc.id or f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": fc.name,
                        "arguments": json.dumps(fc.args or {}, ensure_ascii=False),
                    },
                }
                for fc in function_calls
            ]

        return msg

    def _parse_response_to_parts(self, response_text: str) -> list[types.Part]:
        """Parse sglang raw text into ADK Parts (text + function calls).

        Tries to detect tool calls from common patterns:
        1. Qwen-style <tool_call> XML tags
        2. JSON objects with 'name' + 'arguments' keys
        3. Falls back to plain text
        """
        parts: list[types.Part] = []

        tool_call_pattern = re.compile(
            r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL
        )
        matches = tool_call_pattern.findall(response_text)

        if matches:
            # Extract any text before the first tool call
            first_match_pos = response_text.find("<tool_call>")
            if first_match_pos > 0:
                preamble = response_text[:first_match_pos].strip()
                if preamble:
                    parts.append(types.Part(text=preamble))

            for match_str in matches:
                try:
                    call_data = json.loads(match_str)
                    name = call_data.get("name", "")
                    arguments = call_data.get("arguments", {})
                    if isinstance(arguments, str):
                        arguments = json.loads(arguments)
                    part = types.Part.from_function_call(
                        name=name,
                        args=arguments,
                    )
                    if part.function_call is not None:
                        part.function_call.id = f"call_{uuid.uuid4().hex[:8]}"
                    parts.append(part)
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(
                        f"Failed to parse tool call: {match_str[:100]}... Error: {e}"
                    )
                    parts.append(types.Part(text=match_str))
            return parts

        # Try generic JSON tool call detection ({"name": ..., "arguments": ...})
        try:
            # Look for JSON at the end of response
            stripped = response_text.strip()
            if (
                stripped.startswith("{")
                and "name" in stripped
                and "arguments" in stripped
            ):
                call_data = json.loads(stripped)
                if "name" in call_data and "arguments" in call_data:
                    arguments = call_data["arguments"]
                    if isinstance(arguments, str):
                        arguments = json.loads(arguments)
                    part = types.Part.from_function_call(
                        name=call_data["name"],
                        args=arguments,
                    )
                    if part.function_call is not None:
                        part.function_call.id = f"call_{uuid.uuid4().hex[:8]}"
                    return [part]
        except (json.JSONDecodeError, KeyError):
            pass

        # Fallback: plain text
        if response_text.strip():
            parts.append(types.Part(text=response_text))
        return parts

    def _get_token_delta(
        self,
        messages: list[dict[str, Any]],
        last_role: str,
    ) -> tuple[list[int], list[int]]:
        """Calculate the token delta for the latest message.

        This mirrors tau-bench's _get_token_delta logic:
        - assistant messages → loss_mask = 1 (trainable)
        - tool/user/system messages → loss_mask = 0 (not trainable)
        """
        curr = self._tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=False,
            tools=self._tools_info if self._tools_info else None,
        )

        if last_role == "assistant":
            prev = self._tokenizer.apply_chat_template(
                messages[:-1],
                tokenize=False,
                add_generation_prompt=True,
                tools=self._tools_info if self._tools_info else None,
            )
            new_tokens = self._tokenizer.encode(
                curr[len(prev) :], add_special_tokens=False
            )
            return new_tokens, [1] * len(new_tokens)
        else:
            prev = self._tokenizer.apply_chat_template(
                messages[:-1],
                tokenize=False,
                add_generation_prompt=False,
                tools=self._tools_info if self._tools_info else None,
            )
            new_tokens = self._tokenizer.encode(
                curr[len(prev) :], add_special_tokens=False
            )
            return new_tokens, [0] * len(new_tokens)
