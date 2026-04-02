"""Code domain configuration for the memory system."""

from opensage.memory.database_based.long_term.config.domain_config import (
    DomainConfig,
    register_domain,
)
from opensage.memory.database_based.long_term.schema.node_types import (
    CLASS_NODE,
    FILE_NODE,
    FUNCTION_NODE,
)
from opensage.memory.database_based.long_term.schema.relationship_types import (
    CALLS_RELATIONSHIP,
    CONTAINS_RELATIONSHIP,
    MENTIONS_RELATIONSHIP,
)

CODE_DOMAIN_CONFIG = DomainConfig(
    name="code",
    description="Code understanding domain with functions, classes, and files",
    node_types={
        "Function": FUNCTION_NODE,
        "Class": CLASS_NODE,
        "File": FILE_NODE,
    },
    relationships={
        "CONTAINS": CONTAINS_RELATIONSHIP,
        "CALLS": CALLS_RELATIONSHIP,
        "MENTIONS": MENTIONS_RELATIONSHIP,
    },
    search_strategies=[
        "keyword_search",
        "embedding_search",
    ],
    default_strategy="keyword_search",
)

register_domain(CODE_DOMAIN_CONFIG)
