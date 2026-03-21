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

Apply once at startup via :func:`apply`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_patched: bool = False
_orig_generate_content_async = None
_orig_model_response_to_generate_content_response = None
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
    global _orig_transform_parsed_response
    if _patched:
        return

    from google.adk.models import lite_llm as _lite_llm_module
    from google.adk.models.lite_llm import LiteLlm

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

    def _patched_model_response_to_gcr(response):
        llm_response = _orig_model_response_to_generate_content_response(response)

        # Extract web search metadata from Anthropic's raw content blocks
        hidden = getattr(response, "_hidden_params", None)
        if isinstance(hidden, dict):
            original = hidden.get("original_response")
            if isinstance(original, list):
                metadata = _extract_grounding_metadata(original)
                if metadata is not None:
                    llm_response.grounding_metadata = metadata

        return llm_response

    _lite_llm_module._model_response_to_generate_content_response = (
        _patched_model_response_to_gcr
    )

    _patched = True
    logger.debug("litellm_web_search patch applied")
