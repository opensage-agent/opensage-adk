"""
Graph-based Adaptive Memory System for AIgiSE.

This module provides a rich graph-based memory system with:
- Domain Configuration Layer: Pluggable node types, relationships, search strategies
- Core Memory Engine: LLM-driven search & update with entity extraction
- Graph Storage: Neo4j with vector embeddings
- Observer Module: Async tool result monitoring and storage
"""

from aigise.memory.config.domain_config import DomainConfig
from aigise.memory.schema.node_types import MatchingRule, NodeTypeConfig, PropertyConfig
from aigise.memory.schema.relationship_types import RelationshipConfig
from aigise.memory.storage_decider import StorageDecider, StorageDecision

__all__ = [
    "DomainConfig",
    "NodeTypeConfig",
    "MatchingRule",
    "PropertyConfig",
    "RelationshipConfig",
    "StorageDecider",
    "StorageDecision",
]
