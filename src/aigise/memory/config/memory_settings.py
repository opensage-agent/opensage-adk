"""Centralized settings for the memory module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from opensage.config.config_dataclass import MemoryConfig


@dataclass
class MemorySettings:
    """Configuration settings for the memory module.

    These settings are loaded from the TOML configuration file via OpenSageConfig.
    The [memory] section in config controls these values.
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
    def from_config(cls, memory_config: "MemoryConfig") -> "MemorySettings":
        """Create MemorySettings from a MemoryConfig dataclass.

        Args:
            memory_config ('MemoryConfig'): The MemoryConfig from OpenSageConfig.
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


def configure_memory_from_config(memory_config: "MemoryConfig") -> MemorySettings:
    """Configure memory settings from a MemoryConfig dataclass.

    This is typically called during session initialization with the
    memory config from OpenSageConfig.

    Args:
        memory_config ('MemoryConfig'): The MemoryConfig from OpenSageConfig.
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


def is_memory_enabled() -> bool:
    """Check if memory module is enabled.

    Returns:
        bool: True if memory is enabled, False otherwise.
    """
    return get_memory_settings().enabled
