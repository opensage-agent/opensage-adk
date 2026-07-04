"""Configure LiteLLM retry defaults used by ADK LiteLlm."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_patched = False

DEFAULT_NUM_RETRIES = 15


def apply() -> None:
    """Set LiteLLM's global retry default for direct ADK acompletion calls."""
    global _patched

    import litellm

    if litellm.num_retries is None:
        litellm.num_retries = DEFAULT_NUM_RETRIES

    if _patched:
        return

    _patched = True
    logger.info("litellm_retry defaults applied: num_retries=%s", litellm.num_retries)
