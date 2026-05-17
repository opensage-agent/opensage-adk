"""Patch LiteLLM direct retry waits used by ADK LiteLlm."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import tenacity

logger = logging.getLogger(__name__)

_patched = False

DEFAULT_NUM_RETRIES = 15
INITIAL_DELAY_SECONDS = 2
MAX_DELAY_SECONDS = 60
JITTER_SECONDS = 1


def _retry_wait():
    return tenacity.wait_exponential_jitter(
        initial=INITIAL_DELAY_SECONDS,
        max=MAX_DELAY_SECONDS,
        jitter=JITTER_SECONDS,
    )


def _sync_sleep(delay: float) -> None:
    time.sleep(delay)


async def _async_sleep(delay: float) -> None:
    await asyncio.sleep(delay)


def apply() -> None:
    """Use exponential backoff + jitter for LiteLLM direct retries."""
    global _patched

    import litellm

    if litellm.num_retries is None:
        litellm.num_retries = DEFAULT_NUM_RETRIES

    if _patched:
        return

    from litellm import main as litellm_main

    if litellm.num_retries is None:
        litellm.num_retries = DEFAULT_NUM_RETRIES

    def completion_with_retries(*args: Any, **kwargs: Any):
        num_retries = kwargs.pop("num_retries", DEFAULT_NUM_RETRIES)
        kwargs["max_retries"] = 0
        kwargs["num_retries"] = 0
        kwargs.pop("retry_strategy", None)
        original_function = kwargs.pop("original_function", litellm_main.completion)
        if not num_retries:
            return original_function(*args, **kwargs)
        retryer = tenacity.Retrying(
            wait=_retry_wait(),
            stop=tenacity.stop_after_attempt(int(num_retries)),
            reraise=True,
            sleep=_sync_sleep,
        )
        return retryer(original_function, *args, **kwargs)

    async def acompletion_with_retries(*args: Any, **kwargs: Any):
        num_retries = kwargs.pop("num_retries", DEFAULT_NUM_RETRIES)
        kwargs["max_retries"] = 0
        kwargs["num_retries"] = 0
        kwargs.pop("retry_strategy", None)
        original_function = kwargs.pop("original_function", litellm_main.completion)
        if not num_retries:
            result = original_function(*args, **kwargs)
            if hasattr(result, "__await__"):
                return await result
            return result
        retryer = tenacity.AsyncRetrying(
            wait=_retry_wait(),
            stop=tenacity.stop_after_attempt(int(num_retries)),
            reraise=True,
            sleep=_async_sleep,
        )
        return await retryer(original_function, *args, **kwargs)

    litellm.completion_with_retries = completion_with_retries
    litellm.acompletion_with_retries = acompletion_with_retries
    litellm_main.completion_with_retries = completion_with_retries
    litellm_main.acompletion_with_retries = acompletion_with_retries

    _patched = True
    logger.info("litellm_retry patch applied")
