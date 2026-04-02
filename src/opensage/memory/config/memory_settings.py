"""Centralized settings for the memory module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from opensage.config.config_dataclass import LongTermMemoryConfig


@dataclass
class MemorySettings:
    """Configuration settings for long-term database memory.

    These settings are loaded from the TOML configuration file via OpenSageConfig.
    The [memory.database.long_term] section in config controls these values.
    """

    # Whether memory module is enabled (default: disabled)
    enabled: bool = False

    # LLM model for internal operations (strategy selection, entity extraction, etc.)
    llm_model: str = "gemini/gemini-2.5-flash-lite"

    # Embedding model for vector search
    embedding_model: str = "gemini/gemini-embedding-001"

    # Whether to use LLM for search strategy selection
    use_llm_selection: bool = True

    # Whether to use LLM for operation type decisions (ADD/UPDATE/DELETE/NONE)
    use_llm_decision: bool = False

    # Max iterations for search refinement
    search_max_iterations: int = 3

    # Similarity threshold for relationship discovery
    similarity_threshold: float = 0.7

    @classmethod
    def from_config(cls, memory_config: "LongTermMemoryConfig") -> "MemorySettings":
        """Create MemorySettings from long-term database memory config.

        Args:
            memory_config ('LongTermMemoryConfig'): Long-term memory config.
        Returns:
            'MemorySettings': MemorySettings instance with values from config.
        """
        return cls(
            enabled=memory_config.enabled,
            llm_model=memory_config.llm_model,
            embedding_model=memory_config.embedding_model,
            use_llm_selection=memory_config.use_llm_selection,
            use_llm_decision=memory_config.use_llm_decision,
            search_max_iterations=memory_config.search_max_iterations,
            similarity_threshold=memory_config.similarity_threshold,
        )


# Global singleton instance
_settings: Optional[MemorySettings] = None


def get_memory_settings() -> MemorySettings:
    """Get the global memory settings instance.

    Returns:
        MemorySettings: The singleton settings instance.
    """
    global _settings
    if _settings is None:
        _settings = MemorySettings()
    return _settings


def configure_memory_from_config(
    memory_config: "LongTermMemoryConfig",
) -> MemorySettings:
    """Configure memory settings from long-term database memory config.

    This is typically called during session initialization with the
    long-term database memory config from OpenSageConfig.

    Args:
        memory_config ('LongTermMemoryConfig'): Long-term memory config.
    Returns:
        MemorySettings: The configured settings instance.
    """
    global _settings
    _settings = MemorySettings.from_config(memory_config)
    return _settings


def reset_memory_settings() -> None:
    """Reset memory settings to defaults.

    Useful for testing or reconfiguration.
    """
    global _settings
    _settings = None
