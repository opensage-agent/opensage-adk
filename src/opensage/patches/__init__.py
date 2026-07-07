from __future__ import annotations

from .adk_llm_compaction import apply as _apply_compaction
from .adk_tool_not_found import apply as _apply_tool_not_found
from .agent_run_async import apply as _apply_agent_run_async
from .litellm_retry import apply as _apply_litellm_retry
from .litellm_web_search import apply as _apply_litellm_web_search


def apply_all() -> None:
    # Install wrappers/patches once; runtime toggles are respected.
    _apply_agent_run_async()
    _apply_compaction()
    _apply_litellm_retry()
    _apply_litellm_web_search()
    _apply_tool_not_found()
