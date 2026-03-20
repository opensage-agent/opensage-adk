"""Relationship type configurations for the memory graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from opensage.memory.schema.node_types import PropertyConfig, PropertyType


@dataclass
class RelationshipConfig:
    """Configuration for a relationship type in the memory graph.

    Relationships connect nodes and can have their own properties.
    This configuration defines the allowed source/target node types
    and any properties on the relationship itself.
    """

    type_name: str
    """Neo4j relationship type (e.g., 'ABOUT', 'HAS_ANSWER', 'RELATED_TO')."""

    source_types: List[str]
    """Allowed source node labels."""

    target_types: List[str]
    """Allowed target node labels."""

    properties: Dict[str, PropertyConfig] = field(default_factory=dict)
    """Property configurations for the relationship."""

    bidirectional: bool = False
    """If True, the relationship is semantically bidirectional."""

    auto_create: bool = False
    """If True, this relationship is automatically created during entity extraction."""

    description: str = ""
    """Human-readable description of this relationship type."""

    def __post_init__(self):
        """Add default properties if not present."""
        # Always add created_at if not specified
        if "created_at" not in self.properties:
            self.properties["created_at"] = PropertyConfig(
                name="created_at",
                property_type=PropertyType.DATETIME,
                description="When this relationship was created",
            )

    def is_valid_connection(self, source_type: str, target_type: str) -> bool:
        """Check if a connection is valid for this relationship type."""
        return source_type in self.source_types and target_type in self.target_types


# Pre-defined common relationship types

ABOUT_RELATIONSHIP = RelationshipConfig(
    type_name="ABOUT",
    source_types=["Question"],
    target_types=["Function", "Class", "File", "Topic"],
    properties={
        "confidence": PropertyConfig(
            name="confidence",
            property_type=PropertyType.FLOAT,
            description="Confidence score for this relationship (0.0 to 1.0)",
        ),
    },
    auto_create=True,
    description="Indicates what a question is about",
)

HAS_ANSWER_RELATIONSHIP = RelationshipConfig(
    type_name="HAS_ANSWER",
    source_types=["Question"],
    target_types=["Answer"],
    properties={
        "is_primary": PropertyConfig(
            name="is_primary",
            property_type=PropertyType.BOOLEAN,
            default=True,
            description="Whether this is the primary answer",
        ),
    },
    auto_create=True,
    description="Links a question to its answer(s)",
)

HAS_TOPIC_RELATIONSHIP = RelationshipConfig(
    type_name="HAS_TOPIC",
    source_types=["Question", "Answer"],
    target_types=["Topic"],
    properties={
        "relevance": PropertyConfig(
            name="relevance",
            property_type=PropertyType.FLOAT,
            description="How relevant the topic is (0.0 to 1.0)",
        ),
    },
    auto_create=True,
    description="Associates content with semantic topics",
)

RELATED_TO_RELATIONSHIP = RelationshipConfig(
    type_name="RELATED_TO",
    source_types=["Question", "Topic", "Function", "Class"],
    target_types=["Question", "Topic", "Function", "Class"],
    properties={
        "similarity": PropertyConfig(
            name="similarity",
            property_type=PropertyType.FLOAT,
            description="Similarity score between the two entities",
        ),
        "relationship_type": PropertyConfig(
            name="relationship_type",
            property_type=PropertyType.STRING,
            description="Specific type of relationship (e.g., 'similar', 'prerequisite')",
        ),
    },
    bidirectional=True,
    description="General relationship between similar or related entities",
)

CONTAINS_RELATIONSHIP = RelationshipConfig(
    type_name="CONTAINS",
    source_types=["File", "Class"],
    target_types=["Function", "Class"],
    description="Indicates containment (file contains function, class contains method)",
)

CALLS_RELATIONSHIP = RelationshipConfig(
    type_name="CALLS",
    source_types=["Function"],
    target_types=["Function"],
    properties={
        "call_count": PropertyConfig(
            name="call_count",
            property_type=PropertyType.INTEGER,
            default=1,
            description="Number of call sites",
        ),
    },
    description="Function call relationship (from CPG)",
)

MENTIONS_RELATIONSHIP = RelationshipConfig(
    type_name="MENTIONS",
    source_types=["Question", "Answer"],
    target_types=["Function", "Class", "File"],
    properties={
        "context": PropertyConfig(
            name="context",
            property_type=PropertyType.STRING,
            description="Context of the mention",
        ),
    },
    auto_create=True,
    description="Indicates that content mentions a code entity",
)


# Registry of all relationship types
RELATIONSHIP_REGISTRY: Dict[str, RelationshipConfig] = {
    "ABOUT": ABOUT_RELATIONSHIP,
    "HAS_ANSWER": HAS_ANSWER_RELATIONSHIP,
    "HAS_TOPIC": HAS_TOPIC_RELATIONSHIP,
    "RELATED_TO": RELATED_TO_RELATIONSHIP,
    "CONTAINS": CONTAINS_RELATIONSHIP,
    "CALLS": CALLS_RELATIONSHIP,
    "MENTIONS": MENTIONS_RELATIONSHIP,
}


def get_relationship_config(type_name: str) -> Optional[RelationshipConfig]:
    """Get a relationship configuration by its type name."""
    return RELATIONSHIP_REGISTRY.get(type_name)


def register_relationship(config: RelationshipConfig) -> None:
    """Register a new relationship type configuration."""
    RELATIONSHIP_REGISTRY[config.type_name] = config
