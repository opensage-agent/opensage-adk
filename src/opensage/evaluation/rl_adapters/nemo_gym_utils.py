"""
Utilities for converting OpenSage ADK session data to NeMo Gym response format.

Converts ADK events + NemoLlm logprobs → NeMo Gym output items,
equivalent to Harbor's HarborAgentUtils.trajectory_to_responses().
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Get attribute from ADK object or key from dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def events_to_nemo_gym_output(
    events: list,
    logprob_turns: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert ADK session events + logprob data to NeMo Gym output items.

    Accepts both ADK Event objects (in-process) and JSON-deserialized
    event dicts (from session_trace.json).

    Args:
        events: List of ADK Event objects or event dicts
        logprob_turns: List of dicts with prompt_token_ids, generation_token_ids,
            generation_log_probs (from NemoLlm.get_logprob_turns())

    Returns:
        List of NeMo Gym output item dicts ready for NeMoGymResponse.output
    """
    output_items = []
    llm_call_index = 0

    for event in events:
        # Handle both ADK objects and JSON dicts
        content = _get(event, "content")
        if not content:
            continue
        parts = _get(content, "parts")
        if not parts:
            continue

        role = _get(content, "role", "")
        if role == "user":
            continue

        text_parts = []
        function_calls = []
        function_responses = []

        for part in parts:
            fc = _get(part, "function_call") or _get(part, "functionCall")
            fr = _get(part, "function_response") or _get(part, "functionResponse")
            text = _get(part, "text")

            if fc:
                function_calls.append(
                    {
                        "call_id": _get(fc, "id", "") or f"call_{uuid4().hex[:8]}",
                        "name": _get(fc, "name", ""),
                        "arguments": json.dumps(_get(fc, "args", {}) or {}),
                    }
                )
            elif fr:
                response_data = _get(fr, "response", "")
                response_str = (
                    json.dumps(response_data)
                    if isinstance(response_data, dict)
                    else str(response_data)
                )
                function_responses.append(
                    {
                        "call_id": _get(fr, "id", "") or f"call_{uuid4().hex[:8]}",
                        "name": _get(fr, "name", ""),
                        "output": response_str,
                    }
                )
            elif text:
                thought = _get(part, "thought", False)
                if not thought:
                    text_parts.append(text)

        if (text_parts or function_calls) and role == "model":
            text = "\n".join(text_parts)

            logprob_data = None
            if llm_call_index < len(logprob_turns):
                logprob_data = logprob_turns[llm_call_index]
                llm_call_index += 1

            message_item = _build_message_item(text, logprob_data)
            output_items.append(message_item)

            for fc in function_calls:
                output_items.append(
                    {
                        "type": "function_call",
                        "id": f"fc_{uuid4().hex[:8]}",
                        "call_id": fc["call_id"],
                        "name": fc["name"],
                        "arguments": fc["arguments"],
                        "status": "completed",
                    }
                )

        for fr in function_responses:
            output_items.append(
                {
                    "type": "function_call_output",
                    "id": f"fco_{uuid4().hex[:8]}",
                    "call_id": fr["call_id"],
                    "output": fr["output"],
                }
            )

    return output_items


def extract_usage_from_events(events: list) -> Dict[str, Any]:
    """Extract token usage from ADK session events (objects or dicts)."""
    input_tokens = 0
    output_tokens = 0
    cached_tokens = 0

    for event in events:
        usage = _get(event, "usage_metadata") or _get(event, "usageMetadata")
        if usage:
            input_tokens += (
                _get(usage, "prompt_token_count", 0)
                or _get(usage, "promptTokenCount", 0)
                or 0
            )
            output_tokens += (
                _get(usage, "candidates_token_count", 0)
                or _get(usage, "candidatesTokenCount", 0)
                or 0
            )
            cached_tokens += (
                _get(usage, "cached_content_token_count", 0)
                or _get(usage, "cachedContentTokenCount", 0)
                or 0
            )

    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": cached_tokens},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": input_tokens + output_tokens,
    }


def extract_input_from_events(events: list) -> List[Dict[str, Any]]:
    """Extract initial user messages from ADK events (objects or dicts)."""
    input_messages = []
    for event in events:
        content = _get(event, "content")
        if not content:
            continue
        role = _get(content, "role", "")
        if role == "user":
            parts = _get(content, "parts", [])
            for part in parts:
                text = _get(part, "text")
                if text:
                    input_messages.append(
                        {
                            "role": "user",
                            "content": text,
                            "type": "message",
                        }
                    )
        elif role == "model":
            break
    return input_messages


def _build_message_item(
    text: str,
    logprob_data: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a NeMo Gym output message item, with or without logprob data."""
    content = [
        {
            "type": "output_text",
            "annotations": [],
            "text": text,
            "logprobs": None,
        }
    ]

    item = {
        "id": f"cht_{uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": content,
    }

    if logprob_data:
        item["prompt_token_ids"] = logprob_data.get("prompt_token_ids", [])
        item["generation_token_ids"] = logprob_data.get("generation_token_ids", [])
        item["generation_log_probs"] = logprob_data.get("generation_log_probs", [])

    return item
