"""Monkey-patch LiteLlm to support server-side web search.

Three patches are applied:

1. ``LiteLlm.generate_content_async`` — Tools like ``WebSearchTool`` set
   ``llm_request._extra_completion_kwargs`` during ``process_llm_request``.
   The patch reads that attribute and temporarily merges the extra kwargs
   into ``self._additional_args`` so they are forwarded to
   ``litellm.acompletion()``.

2. ``AnthropicConfig.transform_parsed_response`` — Fixes a litellm bug where
   ``_hidden_params`` is overwritten at the end of the method, losing the
   ``original_response`` (raw content blocks) that was set earlier.  The
   patch preserves ``original_response`` in the final ``_hidden_params``.

3. ``_model_response_to_generate_content_response`` — Extracts web search
   metadata from litellm's ``_hidden_params["original_response"]`` (the raw
   Anthropic content blocks) and populates ``LlmResponse.grounding_metadata``
   so the search queries and result URLs are captured in the agent trajectory.

4. ``_message_to_generate_content_response`` — Parses each returned tool call
   independently.  If a provider returns malformed JSON for one tool call's
   arguments, the bad call is dropped and recorded in response metadata instead
   of crashing the whole agent turn.

Apply once at startup via :func:`apply`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_patched: bool = False
_orig_generate_content_async = None
_orig_model_response_to_generate_content_response = None
_orig_message_to_generate_content_response = None
_orig_transform_parsed_response = None


def _extract_grounding_metadata(
    content_blocks: List[Dict[str, Any]],
) -> Any:
    """Extract web search metadata from raw Anthropic content blocks.

    Converts ``server_tool_use`` (search queries) and
    ``web_search_tool_result`` (result URLs/titles) blocks into a
    ``types.GroundingMetadata`` compatible with ADK's event logging.

    Returns None if no web search blocks are found.
    """
    from google.genai import types

    search_queries: List[str] = []
    grounding_chunks: List[types.GroundingChunk] = []

    # Anthropic's web search response contains these content block types:
    #   - "server_tool_use" with name="web_search": the search query
    #   - "web_search_tool_result": search results with URLs and titles
    for block in content_blocks:
        block_type = block.get("type")

        if block_type == "server_tool_use" and block.get("name") == "web_search":
            query = (block.get("input") or {}).get("query")
            if query:
                search_queries.append(query)

        elif block_type == "web_search_tool_result":
            for result in block.get("content") or []:
                if result.get("type") == "web_search_result":
                    url = result.get("url", "")
                    domain = ""
                    try:
                        domain = urlparse(url).netloc
                    except Exception:
                        pass
                    grounding_chunks.append(
                        types.GroundingChunk(
                            web=types.GroundingChunkWeb(
                                uri=url,
                                title=result.get("title", ""),
                                domain=domain,
                            )
                        )
                    )

    if not search_queries and not grounding_chunks:
        return None

    return types.GroundingMetadata(
        web_search_queries=search_queries or None,
        grounding_chunks=grounding_chunks or None,
    )


def apply() -> None:
    """Monkey-patch ``LiteLlm`` for web search support (idempotent)."""
    global _patched, _orig_generate_content_async
    global _orig_model_response_to_generate_content_response
    global _orig_message_to_generate_content_response
    global _orig_transform_parsed_response
    if _patched:
        return

    from google.adk.models import lite_llm as _lite_llm_module
    from google.adk.models.lite_llm import LiteLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    # --- Patch 1: forward extra completion kwargs ---

    _orig_generate_content_async = LiteLlm.generate_content_async

    async def _wrapped_generate_content_async(self, llm_request, stream=False):
        extra_kwargs = getattr(llm_request, "_extra_completion_kwargs", None)
        if extra_kwargs:
            saved = self._additional_args
            self._additional_args = {**saved, **extra_kwargs}
            try:
                async for resp in _orig_generate_content_async(
                    self, llm_request, stream=stream
                ):
                    yield resp
            finally:
                self._additional_args = saved
        else:
            async for resp in _orig_generate_content_async(
                self, llm_request, stream=stream
            ):
                yield resp

    LiteLlm.generate_content_async = _wrapped_generate_content_async

    # --- Patch 2: fix litellm bug that loses original_response ---
    # In transform_parsed_response, litellm sets
    #   model_response._hidden_params["original_response"] = content_blocks
    # but then overwrites model_response._hidden_params with a local dict,
    # losing original_response.  We patch to preserve it.

    from litellm.llms.anthropic.chat.transformation import AnthropicConfig

    _orig_transform_parsed_response = AnthropicConfig.transform_parsed_response

    def _patched_transform_parsed_response(self, *args, **kwargs):
        result = _orig_transform_parsed_response(self, *args, **kwargs)
        # result is None; the method mutates model_response in place.
        # Re-extract content blocks from completion_response and store them.
        # The signature is (completion_response, raw_response, model_response, ...)
        if args:
            completion_response = args[0]
        else:
            completion_response = kwargs.get("completion_response")
        if args and len(args) >= 3:
            model_response = args[2]
        else:
            model_response = kwargs.get("model_response")

        if (
            model_response is not None
            and isinstance(completion_response, dict)
            and "content" in completion_response
        ):
            hidden = getattr(model_response, "_hidden_params", None)
            if isinstance(hidden, dict):
                hidden["original_response"] = completion_response["content"]

        return result

    AnthropicConfig.transform_parsed_response = _patched_transform_parsed_response

    # --- Patch 3: extract grounding metadata from raw response ---

    _orig_model_response_to_generate_content_response = (
        _lite_llm_module._model_response_to_generate_content_response
    )
    _orig_message_to_generate_content_response = (
        _lite_llm_module._message_to_generate_content_response
    )

    def _get_value(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _preview_tool_args(raw_args: Any) -> str:
        if isinstance(raw_args, str):
            text = raw_args
        else:
            text = repr(raw_args)
        text = text.replace("\n", "\\n")
        if len(text) > 200:
            return f"{text[:200]}..."
        return text

    def _parse_tool_call_args(
        tool_call: Any,
    ) -> tuple[dict[str, Any] | None, str | None]:
        function = _get_value(tool_call, "function")
        raw_args = _get_value(function, "arguments", "{}")

        if raw_args in (None, ""):
            return {}, None
        if isinstance(raw_args, dict):
            return raw_args, None
        if not isinstance(raw_args, str):
            return None, f"expected JSON string, got {type(raw_args).__name__}"

        try:
            parsed_args = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            return None, str(exc)

        if not isinstance(parsed_args, dict):
            return None, f"expected JSON object, got {type(parsed_args).__name__}"

        return parsed_args, None

    def _patched_message_to_gcr(
        message,
        *,
        is_partial: bool = False,
        model_version: str = None,
        thought_parts: Optional[List[types.Part]] = None,
    ) -> LlmResponse:
        _lite_llm_module._ensure_litellm_imported()

        parts: List[types.Part] = []
        if not thought_parts:
            thought_parts = _lite_llm_module._convert_reasoning_value_to_parts(
                _lite_llm_module._extract_reasoning_value(message)
            )
        if thought_parts:
            parts.extend(thought_parts)

        message_content, tool_calls = (
            _lite_llm_module._split_message_content_and_tool_calls(message)
        )
        if isinstance(message_content, str) and message_content:
            parts.append(types.Part.from_text(text=message_content))

        malformed_tool_calls: List[Dict[str, str]] = []
        if tool_calls:
            for tool_call in tool_calls:
                if _get_value(tool_call, "type") != "function":
                    continue

                function = _get_value(tool_call, "function")
                tool_name = _get_value(function, "name")
                tool_call_id = _get_value(tool_call, "id")
                parsed_args, error = _parse_tool_call_args(tool_call)

                if not isinstance(tool_name, str) or not tool_name:
                    error = error or "missing function name"

                if error is not None:
                    raw_args = _get_value(function, "arguments", "")
                    malformed_tool_calls.append(
                        {
                            "id": str(tool_call_id or ""),
                            "name": str(tool_name or ""),
                            "error": error,
                            "arguments_preview": _preview_tool_args(raw_args),
                        }
                    )
                    continue

                part = types.Part.from_function_call(
                    name=tool_name,
                    args=parsed_args or {},
                )
                part.function_call.id = tool_call_id
                parts.append(part)

        custom_metadata = None
        if malformed_tool_calls:
            logger.warning(
                "Skipping %d malformed LiteLLM tool call(s): %s",
                len(malformed_tool_calls),
                malformed_tool_calls,
            )
            custom_metadata = {"malformed_tool_calls": malformed_tool_calls}
            if not parts:
                parts.append(
                    types.Part.from_text(
                        text=(
                            "The previous tool call could not be executed because "
                            "its arguments were not valid JSON. Retry with complete "
                            "JSON arguments."
                        )
                    )
                )

        return LlmResponse(
            content=types.Content(role="model", parts=parts),
            partial=is_partial,
            model_version=model_version,
            custom_metadata=custom_metadata,
        )

    _lite_llm_module._message_to_generate_content_response = _patched_message_to_gcr

    def _collect_server_tool_ids(content_blocks: List[Dict[str, Any]]) -> set:
        """Return the set of tool-call IDs that originated from server_tool_use blocks."""
        ids: set = set()
        for block in content_blocks:
            if block.get("type") == "server_tool_use":
                block_id = block.get("id")
                if block_id:
                    ids.add(block_id)
        return ids

    def _patched_model_response_to_gcr(response):
        llm_response = _orig_model_response_to_generate_content_response(response)

        server_tool_ids: set = set()

        # Extract web search metadata from Anthropic's raw content blocks
        hidden = getattr(response, "_hidden_params", None)
        if isinstance(hidden, dict):
            original = hidden.get("original_response")
            if isinstance(original, list):
                metadata = _extract_grounding_metadata(original)
                if metadata is not None:
                    llm_response.grounding_metadata = metadata
                server_tool_ids = _collect_server_tool_ids(original)

        # Strip server-side tool FunctionCall parts that litellm converted
        # from server_tool_use blocks.  These are already executed by the
        # provider and must not be dispatched by ADK.  We match by tool-call
        # ID so user-defined tools with the same name are not affected.
        if server_tool_ids and llm_response.content and llm_response.content.parts:
            llm_response.content.parts = [
                p
                for p in llm_response.content.parts
                if not (p.function_call and p.function_call.id in server_tool_ids)
            ]

        return llm_response

    _lite_llm_module._model_response_to_generate_content_response = (
        _patched_model_response_to_gcr
    )

    _patched = True
    logger.debug("litellm_web_search patch applied")
