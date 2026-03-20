"""Domain configuration for the memory system."""

from opensage.memory.config.code_domain import CODE_DOMAIN_CONFIG
from opensage.memory.config.domain_config import (
    DomainConfig,
    get_all_domains,
    get_domain_config,
    get_merged_domain,
    register_domain,
    validate_all_domains,
)
from opensage.memory.config.memory_settings import (
    MemorySettings,
    configure_memory_from_config,
    get_memory_settings,
    is_memory_enabled,
    reset_memory_settings,
)
from opensage.memory.config.qa_domain import QA_DOMAIN_CONFIG

# All domains registered — validate cross-domain relationships now.
validate_all_domains()

__all__ = [
    "DomainConfig",
    "get_domain_config",
    "get_merged_domain",
    "register_domain",
    "validate_all_domains",
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
