"""
LLM-based analysis for str_replace_edit failures.

Inspired by Gemini-CLI's llm-edit-fixer.ts, but simplified to only provide
explanations (no auto-correction). This helps agents understand why their
edit failed.
"""

import hashlib
import logging
from typing import Optional

from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from opensage.session import get_opensage_session
from opensage.utils.agent_utils import (
    INHERIT_MODEL,
    get_opensage_session_id_from_context,
)

logger = logging.getLogger(__name__)

# Cache for failure analysis results (similar to Gemini-CLI's LRU cache)
_analysis_cache: dict[str, str] = {}
MAX_CACHE_SIZE = 50

FAILURE_ANALYSIS_SYSTEM_PROMPT = """You are an expert code-editing assistant analyzing why a search-and-replace operation failed.

Your ONLY task is to explain WHY the edit failed. Do NOT provide corrections or suggestions for fixing it.

Focus on identifying:
1. Whitespace/indentation mismatches (tabs vs spaces, wrong indentation level)
2. Line ending differences
3. Missing or extra characters
4. Context that doesn't exist in the file
5. Multiple matches when unique match was expected
6. Encoding issues (escaped characters)

Be concise and specific. State the exact reason for failure."""

FAILURE_ANALYSIS_USER_PROMPT = """# Failed Edit Analysis

**File path:** {file_path}

**Search string (failed to match):**
```
{old_string}
```

**Replacement string (not applied):**
```
{new_string}
```

**Error message:**
{error_message}

**Current file content:**
```
{file_content}
```

# Your Task
Analyze why the search string failed to match any content in the file. Be specific about the exact cause (whitespace, indentation, missing context, etc.).

Provide a brief, actionable explanation in 1-3 sentences."""


def _get_cache_key(
    file_path: str,
    old_string: str,
    new_string: str,
    error_message: str,
    file_content: str,
) -> str:
    """Generate a cache key for the analysis request."""
    combined = f"{file_path}|{old_string}|{new_string}|{error_message}|{file_content}"
    return hashlib.sha256(combined.encode()).hexdigest()


def _manage_cache(key: str, value: str) -> None:
    """Add to cache, evicting oldest if necessary."""
    global _analysis_cache
    if len(_analysis_cache) >= MAX_CACHE_SIZE:
        # Simple eviction: remove first item (oldest in insertion order for Python 3.7+)
        oldest_key = next(iter(_analysis_cache))
        del _analysis_cache[oldest_key]
    _analysis_cache[key] = value


async def analyze_edit_failure(
    file_path: str,
    old_string: str,
    new_string: str,
    error_message: str,
    file_content: str,
    tool_context: ToolContext,
) -> Optional[str]:
    """
    Analyze why a str_replace_edit operation failed using an LLM.

    Args:
        file_path (str): Path to the file being edited
        old_string (str): The search string that failed to match
        new_string (str): The replacement string that wasn't applied
        error_message (str): The error message from the failed edit
        file_content (str): Current content of the file
    Returns:
        Optional[str]: Analysis explanation string, or None if analysis fails
    """
    # Check cache first
    cache_key = _get_cache_key(
        file_path, old_string, new_string, error_message, file_content
    )
    if cache_key in _analysis_cache:
        logger.debug("Returning cached failure analysis")
        return _analysis_cache[cache_key]

    try:
        # Get session and model configuration
        opensage_session_id = get_opensage_session_id_from_context(tool_context)
        session = get_opensage_session(opensage_session_id)

        # Use flag_claims_model for analysis (or could add dedicated config later)
        model_name = session.config.llm.flag_claims_model
        if not model_name:
            logger.warning("No analysis model configured (flag_claims_model not set)")
            return None

        # Truncate file content if too long to avoid token limits
        max_content_length = 50000  # ~12.5k tokens roughly
        truncated_content = file_content
        if len(file_content) > max_content_length:
            # Try to find relevant section containing potential match
            search_first_line = old_string.split("\n")[0].strip()
            if search_first_line and len(search_first_line) > 10:
                # Find approximate location in file
                idx = file_content.lower().find(search_first_line.lower()[:20])
                if idx != -1:
                    # Extract context around potential match
                    start = max(0, idx - 5000)
                    end = min(len(file_content), idx + max_content_length - 5000)
                    truncated_content = f"[...truncated...]\n{file_content[start:end]}\n[...truncated...]"
                else:
                    # Just take beginning and end
                    truncated_content = (
                        f"{file_content[: max_content_length // 2]}\n"
                        f"[...{len(file_content) - max_content_length} chars truncated...]\n"
                        f"{file_content[-max_content_length // 2 :]}"
                    )
            else:
                truncated_content = (
                    f"{file_content[:max_content_length]}\n"
                    f"[...{len(file_content) - max_content_length} chars truncated...]"
                )

        # Build the prompt
        user_prompt = FAILURE_ANALYSIS_USER_PROMPT.format(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            error_message=error_message,
            file_content=truncated_content,
        )

        # Create LLM request
        llm_request = LlmRequest()
        llm_request.config = types.GenerateContentConfig(
            system_instruction=FAILURE_ANALYSIS_SYSTEM_PROMPT,
        )
        llm_request.contents = [
            types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)])
        ]

        # Resolve model for standalone LLM call.
        # When "inherit", use the session's main model name via LiteLlm rather
        # than reusing the agent's model instance (which may have thinking mode
        # or other config that causes 404 on a bare single-turn call).
        if model_name == INHERIT_MODEL:
            resolved_name = session.config.llm.model_name
            if not resolved_name:
                logger.debug("Cannot resolve model name for edit failure analysis")
                return None
            model = LiteLlm(model=resolved_name)
        else:
            model = LiteLlm(model=model_name)

        # Call model
        analysis_parts = []
        async for llm_response in model.generate_content_async(llm_request):
            if llm_response.content and llm_response.content.parts:
                for part in llm_response.content.parts:
                    if part.text:
                        analysis_parts.append(part.text)

        analysis = "".join(analysis_parts).strip()

        if analysis:
            # Cache the result
            _manage_cache(cache_key, analysis)
            return analysis
        else:
            logger.warning("Empty response from failure analysis model")
            return None

    except Exception as e:
        logger.debug("Failed to analyze edit failure: %s", e)
        return None


def clear_analysis_cache() -> None:
    """Clear the failure analysis cache."""
    global _analysis_cache
    _analysis_cache = {}
