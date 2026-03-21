from __future__ import annotations

from .adk_llm_compaction import apply as _apply_compaction
from .litellm_web_search import apply as _apply_litellm_web_search
from .neo4j_logging import apply as _apply_neo4j_logging


def apply_all() -> None:
    # Install wrappers/patches once; runtime toggles are respected.
    _apply_neo4j_logging()
    _apply_compaction()
    _apply_litellm_web_search()
