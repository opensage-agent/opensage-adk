"""Domain configuration for the memory system."""

from aigise.memory.config.code_domain import CODE_DOMAIN_CONFIG
from aigise.memory.config.domain_config import (
    DomainConfig,
    get_all_domains,
    get_domain_config,
    get_merged_domain,
    register_domain,
)
from aigise.memory.config.memory_settings import (
    MemorySettings,
    configure_memory_from_config,
    get_memory_settings,
    is_memory_enabled,
    reset_memory_settings,
)
from aigise.memory.config.qa_domain import QA_DOMAIN_CONFIG

__all__ = [
    "DomainConfig",
    "get_domain_config",
    "get_merged_domain",
    "register_domain",
    "get_all_domains",
    "CODE_DOMAIN_CONFIG",
    "QA_DOMAIN_CONFIG",
    # Memory settings
    "MemorySettings",
    "get_memory_settings",
    "configure_memory_from_config",
    "reset_memory_settings",
    "is_memory_enabled",
]
