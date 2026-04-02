"""Base domain configuration for the memory system."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from opensage.memory.database_based.long_term.schema.node_types import (
    NodeTypeConfig,
)
from opensage.memory.database_based.long_term.schema.relationship_types import (
    RelationshipConfig,
)

logger = logging.getLogger(__name__)

_DOMAIN_REGISTRY: Dict[str, "DomainConfig"] = {}


@dataclass
class DomainConfig:
    """Configuration for a knowledge domain in the memory system."""

    name: str
    description: str = ""
    node_types: Dict[str, NodeTypeConfig] = field(default_factory=dict)
    relationships: Dict[str, RelationshipConfig] = field(default_factory=dict)
    search_strategies: List[str] = field(default_factory=list)
    default_strategy: str = "embedding_search"
    embedding_dimension: int = 3072

    def __post_init__(self):
        """Validate and register the domain configuration."""
        if not self.search_strategies:
            self.search_strategies = [
                "embedding_search",
                "keyword_search",
                "title_browse",
            ]

    def get_node_type(self, label: str) -> Optional[NodeTypeConfig]:
        """Get a node type configuration by label."""
        return self.node_types.get(label)

    def get_relationship(self, type_name: str) -> Optional[RelationshipConfig]:
        """Get a relationship configuration by type name."""
        return self.relationships.get(type_name)

    def get_node_labels(self) -> List[str]:
        """Get all node labels in this domain."""
        return list(self.node_types.keys())

    def get_relationship_types(self) -> List[str]:
        """Get all relationship types in this domain."""
        return list(self.relationships.keys())

    def get_similarity_searchable_types(self) -> List[str]:
        """Get node types that support similarity search."""
        return [
            label
            for label, config in self.node_types.items()
            if config.supports_similarity_search()
        ]

    def merge_with(self, other: "DomainConfig") -> "DomainConfig":
        """Merge this domain with another, creating a combined configuration."""
        merged_nodes = {**self.node_types, **other.node_types}
        merged_rels = {**self.relationships, **other.relationships}
        merged_strategies = list(
            dict.fromkeys(self.search_strategies + other.search_strategies)
        )

        return DomainConfig(
            name=f"{self.name}+{other.name}",
            description=f"Merged: {self.description} and {other.description}",
            node_types=merged_nodes,
            relationships=merged_rels,
            search_strategies=merged_strategies,
            default_strategy=other.default_strategy or self.default_strategy,
            embedding_dimension=other.embedding_dimension or self.embedding_dimension,
        )

    def validate(self, known_node_types: set[str] | None = None) -> List[str]:
        """Validate the domain configuration."""
        errors = []
        all_types = set(self.node_types) | (known_node_types or set())

        for rel_name, rel_config in self.relationships.items():
            for source in rel_config.source_types:
                if source not in all_types:
                    errors.append(
                        f"Relationship '{rel_name}' references unknown source type "
                        f"'{source}'"
                    )
            for target in rel_config.target_types:
                if target not in all_types:
                    errors.append(
                        f"Relationship '{rel_name}' references unknown target type "
                        f"'{target}'"
                    )

        for label, node_config in self.node_types.items():
            if (
                node_config.supports_similarity_search()
                and not node_config.embedding_property
            ):
                errors.append(
                    f"Node type '{label}' supports similarity search but has no "
                    "embedding_property"
                )

        return errors


def register_domain(config: DomainConfig) -> None:
    """Register a domain configuration globally."""
    _DOMAIN_REGISTRY[config.name] = config
    logger.info("Registered domain: %s", config.name)


def validate_all_domains() -> Dict[str, List[str]]:
    """Validate all registered domains, aware of cross-domain node types."""
    all_types: set[str] = set()
    for domain in _DOMAIN_REGISTRY.values():
        all_types.update(domain.node_types)

    results: Dict[str, List[str]] = {}
    for name, config in _DOMAIN_REGISTRY.items():
        errors = config.validate(known_node_types=all_types)
        if errors:
            logger.warning("Domain '%s' has validation warnings: %s", name, errors)
        results[name] = errors
    return results


def get_domain_config(name: str) -> Optional[DomainConfig]:
    """Get a registered domain configuration by name."""
    return _DOMAIN_REGISTRY.get(name)


def get_all_domains() -> Dict[str, DomainConfig]:
    """Get all registered domain configurations."""
    return dict(_DOMAIN_REGISTRY)


def get_merged_domain(*domain_names: str) -> DomainConfig:
    """Get a merged domain from multiple registered domains."""
    domains = []
    for name in domain_names:
        domain = get_domain_config(name)
        if domain is None:
            raise ValueError(f"Domain '{name}' not found")
        domains.append(domain)

    if not domains:
        raise ValueError("At least one domain name required")

    result = domains[0]
    for domain in domains[1:]:
        result = result.merge_with(domain)

    return result
