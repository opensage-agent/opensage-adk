"""Centralized settings for the memory module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from opensage.config.config_dataclass import LongTermMemoryConfig


@dataclass
class MemorySettings:
    """Configuration settings for long-term database memory."""

    enabled: bool = False
    llm_model: str = "gemini/gemini-2.5-flash-lite"
    embedding_model: str = "gemini/gemini-embedding-001"
    use_llm_selection: bool = True
    use_llm_decision: bool = False
    search_max_iterations: int = 3
    similarity_threshold: float = 0.7

    @classmethod
    def from_config(cls, memory_config: "LongTermMemoryConfig") -> "MemorySettings":
        """Create MemorySettings from long-term database memory config."""
        return cls(
            enabled=memory_config.enabled,
            llm_model=memory_config.llm_model,
            embedding_model=memory_config.embedding_model,
            use_llm_selection=memory_config.use_llm_selection,
            use_llm_decision=memory_config.use_llm_decision,
            search_max_iterations=memory_config.search_max_iterations,
            similarity_threshold=memory_config.similarity_threshold,
        )


_settings: Optional[MemorySettings] = None


def get_memory_settings() -> MemorySettings:
    """Get the global memory settings instance."""
    global _settings
    if _settings is None:
        _settings = MemorySettings()
    return _settings


def configure_memory_from_config(
    memory_config: "LongTermMemoryConfig",
) -> MemorySettings:
    """Configure memory settings from long-term database memory config."""
    global _settings
    _settings = MemorySettings.from_config(memory_config)
    return _settings


def reset_memory_settings() -> None:
    """Reset memory settings to defaults."""
    global _settings
    _settings = None
