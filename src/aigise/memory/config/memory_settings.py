"""Centralized settings for the memory module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from aigise.config.config_dataclass import MemoryConfig


@dataclass
class MemorySettings:
    """Configuration settings for the memory module.

    These settings are loaded from the TOML configuration file via AigiseConfig.
    The [memory] section in config controls these values.
    """

    # Whether memory module is enabled (default: disabled)
    enabled: bool = False

    # LLM model for internal operations (strategy selection, entity extraction, etc.)
    llm_model: str = "gemini-2.5-flash-lite"

    # Embedding model for vector search
    embedding_model: str = "text-embedding-004"

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
            memory_config: The MemoryConfig from AigiseConfig.

        Returns:
            MemorySettings instance with values from config.
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

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "MemorySettings":
        """Create MemorySettings from a dictionary.

        Args:
            config_dict: Dictionary with memory settings.

        Returns:
            MemorySettings instance with values from dict.
        """
        return cls(
            enabled=config_dict.get("enabled", cls.enabled),
            llm_model=config_dict.get("llm_model", cls.llm_model),
            embedding_model=config_dict.get("embedding_model", cls.embedding_model),
            use_llm_selection=config_dict.get(
                "use_llm_selection", cls.use_llm_selection
            ),
            use_llm_decision=config_dict.get("use_llm_decision", cls.use_llm_decision),
            search_max_iterations=config_dict.get(
                "search_max_iterations", cls.search_max_iterations
            ),
            similarity_threshold=config_dict.get(
                "similarity_threshold", cls.similarity_threshold
            ),
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


def configure_memory(
    enabled: Optional[bool] = None,
    llm_model: Optional[str] = None,
    embedding_model: Optional[str] = None,
    use_llm_selection: Optional[bool] = None,
    use_llm_decision: Optional[bool] = None,
    search_max_iterations: Optional[int] = None,
    similarity_threshold: Optional[float] = None,
) -> MemorySettings:
    """Configure the memory module settings programmatically.

    Only non-None values will override the current settings.

    Args:
        enabled: Whether memory module is enabled.
        llm_model: LLM model for internal operations.
        embedding_model: Embedding model for vector search.
        use_llm_selection: Whether to use LLM for strategy selection.
        use_llm_decision: Whether to use LLM for operation decisions.
        search_max_iterations: Max iterations for search refinement.
        similarity_threshold: Threshold for relationship discovery.

    Returns:
        MemorySettings: The updated settings instance.

    Example:
        >>> from aigise.memory.config import configure_memory
        >>> configure_memory(enabled=True, llm_model="gemini-2.0-flash")
    """
    global _settings

    # Start with defaults or existing settings
    current = get_memory_settings()

    # Create new settings with overrides
    _settings = MemorySettings(
        enabled=enabled if enabled is not None else current.enabled,
        llm_model=llm_model if llm_model is not None else current.llm_model,
        embedding_model=embedding_model
        if embedding_model is not None
        else current.embedding_model,
        use_llm_selection=use_llm_selection
        if use_llm_selection is not None
        else current.use_llm_selection,
        use_llm_decision=use_llm_decision
        if use_llm_decision is not None
        else current.use_llm_decision,
        search_max_iterations=search_max_iterations
        if search_max_iterations is not None
        else current.search_max_iterations,
        similarity_threshold=similarity_threshold
        if similarity_threshold is not None
        else current.similarity_threshold,
    )

    return _settings


def configure_memory_from_config(memory_config: "MemoryConfig") -> MemorySettings:
    """Configure memory settings from a MemoryConfig dataclass.

    This is typically called during session initialization with the
    memory config from AigiseConfig.

    Args:
        memory_config: The MemoryConfig from AigiseConfig.

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
        True if memory is enabled, False otherwise.
    """
    return get_memory_settings().enabled
