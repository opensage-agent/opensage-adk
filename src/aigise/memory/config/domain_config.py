"""Base domain configuration for the memory system."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from opensage.memory.schema.node_types import NodeTypeConfig
from opensage.memory.schema.relationship_types import RelationshipConfig

logger = logging.getLogger(__name__)

# Global domain registry
_DOMAIN_REGISTRY: Dict[str, "DomainConfig"] = {}


@dataclass
class DomainConfig:
    """Configuration for a knowledge domain in the memory system.

    A domain defines a coherent set of node types, relationships, and
    search strategies for a particular area of knowledge (e.g., code,
    Q&A pairs, documentation).

    Multiple domains can be combined to create a rich knowledge graph.
    """

    name: str
    """Unique identifier for this domain."""

    description: str = ""
    """Human-readable description of the domain."""

    node_types: Dict[str, NodeTypeConfig] = field(default_factory=dict)
    """Node type configurations keyed by label."""

    relationships: Dict[str, RelationshipConfig] = field(default_factory=dict)
    """Relationship configurations keyed by type name."""

    search_strategies: List[str] = field(default_factory=list)
    """Ordered list of search strategy names to try."""

    default_strategy: str = "embedding_search"
    """Default search strategy if LLM doesn't select one."""

    embedding_dimension: int = 3072
    """Default embedding dimension for this domain (Gemini default)."""

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
        """Merge this domain with another, creating a combined configuration.

        The other domain's configurations take precedence on conflicts.

        Args:
            other ('DomainConfig'): Another domain configuration to merge with.
        Returns:
            'DomainConfig': New merged DomainConfig.
        """
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
        """Validate the domain configuration.

        Args:
            known_node_types (set[str] | None): Node types from other registered domains.
                Cross-domain relationships (e.g. MENTIONS linking qa→code)
                are valid as long as all referenced types exist somewhere.
        Returns:
            List[str]: List of validation error messages (empty if valid).
        """
        errors = []
        all_types = set(self.node_types) | (known_node_types or set())

        # Check relationship source/target types exist (across all domains)
        for rel_name, rel_config in self.relationships.items():
            for source in rel_config.source_types:
                if source not in all_types:
                    errors.append(
                        f"Relationship '{rel_name}' references unknown source type '{source}'"
                    )
            for target in rel_config.target_types:
                if target not in all_types:
                    errors.append(
                        f"Relationship '{rel_name}' references unknown target type '{target}'"
                    )

        # Check node types have valid configurations
        for label, node_config in self.node_types.items():
            if node_config.supports_similarity_search():
                if not node_config.embedding_property:
                    errors.append(
                        f"Node type '{label}' supports similarity search but has no embedding_property"
                    )

        return errors


def register_domain(config: DomainConfig) -> None:
    """Register a domain configuration globally.

    Validation is deferred — call :func:`validate_all_domains` after all
    domains have been registered so that cross-domain relationships
    (e.g. MENTIONS linking qa→code nodes) can be checked correctly.

    Args:
        config (DomainConfig): Domain configuration to register."""
    _DOMAIN_REGISTRY[config.name] = config
    logger.info(f"Registered domain: {config.name}")


def validate_all_domains() -> Dict[str, List[str]]:
    """Validate all registered domains, aware of cross-domain node types.

    Returns:
        Dict[str, List[str]]: Dict mapping domain name → list of validation errors (empty if valid).
    """
    # Collect all known node types across every domain
    all_types: set[str] = set()
    for domain in _DOMAIN_REGISTRY.values():
        all_types.update(domain.node_types)

    results: Dict[str, List[str]] = {}
    for name, config in _DOMAIN_REGISTRY.items():
        errors = config.validate(known_node_types=all_types)
        if errors:
            logger.warning(f"Domain '{name}' has validation warnings: {errors}")
        results[name] = errors
    return results


def get_domain_config(name: str) -> Optional[DomainConfig]:
    """Get a registered domain configuration by name.

    Args:
        name (str): Domain name to look up.
    Returns:
        Optional[DomainConfig]: Domain configuration if found, None otherwise.
    """
    return _DOMAIN_REGISTRY.get(name)


def get_all_domains() -> Dict[str, DomainConfig]:
    """Get all registered domain configurations."""
    return dict(_DOMAIN_REGISTRY)


def get_merged_domain(*domain_names: str) -> DomainConfig:
    """Get a merged domain from multiple registered domains.

    Args:
        *domain_names (str): Names of domains to merge.
    Returns:
        DomainConfig: Merged domain configuration.

    Raises:
        ValueError: If any domain name is not found.
    """
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
