"""Relationship type configurations for the memory graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from opensage.memory.database_based.long_term.schema.node_types import (
    PropertyConfig,
    PropertyType,
)


@dataclass
class RelationshipConfig:
    """Configuration for a relationship type in the memory graph."""

    type_name: str
    source_types: List[str]
    target_types: List[str]
    properties: Dict[str, PropertyConfig] = field(default_factory=dict)
    bidirectional: bool = False
    auto_create: bool = False
    description: str = ""

    def __post_init__(self):
        if "created_at" not in self.properties:
            self.properties["created_at"] = PropertyConfig(
                name="created_at",
                property_type=PropertyType.DATETIME,
                description="When this relationship was created",
            )

    def is_valid_connection(self, source_type: str, target_type: str) -> bool:
        return source_type in self.source_types and target_type in self.target_types


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
    return RELATIONSHIP_REGISTRY.get(type_name)


def register_relationship(config: RelationshipConfig) -> None:
    RELATIONSHIP_REGISTRY[config.type_name] = config
