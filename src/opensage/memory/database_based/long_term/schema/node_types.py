"""Node type configurations for the memory graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class MatchingRule(Enum):
    """How to match nodes when creating relationships."""

    EXACT = "exact"
    SIMILARITY = "similarity"
    HYBRID = "hybrid"


class PropertyType(Enum):
    """Property data types supported by the memory system."""

    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    LIST_STRING = "list_string"
    LIST_FLOAT = "list_float"
    JSON = "json"


@dataclass
class PropertyConfig:
    """Configuration for a single property on a node type."""

    name: str
    property_type: PropertyType
    required: bool = False
    indexed: bool = False
    unique: bool = False
    default: Optional[Any] = None
    description: str = ""


@dataclass
class NodeTypeConfig:
    """Configuration for a node type in the memory graph."""

    label: str
    properties: Dict[str, PropertyConfig] = field(default_factory=dict)
    matching_rule: MatchingRule = MatchingRule.EXACT
    embedding_property: Optional[str] = None
    embedding_dimension: int = 3072
    unique_key: Optional[str] = None
    composite_unique_keys: Optional[List[str]] = None
    similarity_threshold: float = 0.7
    description: str = ""

    def __post_init__(self):
        """Validate configuration after initialization."""
        if self.embedding_property and self.embedding_property not in self.properties:
            self.properties[self.embedding_property] = PropertyConfig(
                name=self.embedding_property,
                property_type=PropertyType.LIST_FLOAT,
                description="Vector embedding for similarity search",
            )

        if self.unique_key and self.unique_key not in self.properties:
            raise ValueError(
                f"unique_key '{self.unique_key}' not found in properties for {self.label}"
            )

        if self.composite_unique_keys:
            for key in self.composite_unique_keys:
                if key not in self.properties:
                    raise ValueError(
                        f"composite_unique_key '{key}' not found in properties for {self.label}"
                    )

    def get_property_names(self) -> List[str]:
        return list(self.properties.keys())

    def get_required_properties(self) -> List[str]:
        return [name for name, config in self.properties.items() if config.required]

    def get_indexed_properties(self) -> List[str]:
        return [name for name, config in self.properties.items() if config.indexed]

    def supports_similarity_search(self) -> bool:
        return self.embedding_property is not None and self.matching_rule in (
            MatchingRule.SIMILARITY,
            MatchingRule.HYBRID,
        )

    def get_merge_keys(self) -> List[str]:
        if self.composite_unique_keys:
            return self.composite_unique_keys
        if self.unique_key:
            return [self.unique_key]
        return self.get_required_properties()


FUNCTION_NODE = NodeTypeConfig(
    label="Function",
    properties={
        "name": PropertyConfig(
            name="name",
            property_type=PropertyType.STRING,
            required=True,
            indexed=True,
            description="Function name",
        ),
        "file_path": PropertyConfig(
            name="file_path",
            property_type=PropertyType.STRING,
            required=True,
            indexed=True,
            description="Path to the file containing this function",
        ),
        "start_line": PropertyConfig(
            name="start_line",
            property_type=PropertyType.INTEGER,
            description="Starting line number in the file",
        ),
        "end_line": PropertyConfig(
            name="end_line",
            property_type=PropertyType.INTEGER,
            description="Ending line number in the file",
        ),
        "signature": PropertyConfig(
            name="signature",
            property_type=PropertyType.STRING,
            description="Function signature",
        ),
        "docstring": PropertyConfig(
            name="docstring",
            property_type=PropertyType.STRING,
            description="Function documentation",
        ),
    },
    matching_rule=MatchingRule.EXACT,
    composite_unique_keys=["name", "file_path"],
    description="A function in the codebase",
)

CLASS_NODE = NodeTypeConfig(
    label="Class",
    properties={
        "name": PropertyConfig(
            name="name",
            property_type=PropertyType.STRING,
            required=True,
            indexed=True,
            description="Class name",
        ),
        "file_path": PropertyConfig(
            name="file_path",
            property_type=PropertyType.STRING,
            required=True,
            indexed=True,
            description="Path to the file containing this class",
        ),
        "start_line": PropertyConfig(
            name="start_line",
            property_type=PropertyType.INTEGER,
            description="Starting line number",
        ),
        "end_line": PropertyConfig(
            name="end_line",
            property_type=PropertyType.INTEGER,
            description="Ending line number",
        ),
        "docstring": PropertyConfig(
            name="docstring",
            property_type=PropertyType.STRING,
            description="Class documentation",
        ),
    },
    matching_rule=MatchingRule.EXACT,
    composite_unique_keys=["name", "file_path"],
    description="A class in the codebase",
)

FILE_NODE = NodeTypeConfig(
    label="File",
    properties={
        "path": PropertyConfig(
            name="path",
            property_type=PropertyType.STRING,
            required=True,
            indexed=True,
            unique=True,
            description="Relative file path",
        ),
        "language": PropertyConfig(
            name="language",
            property_type=PropertyType.STRING,
            indexed=True,
            description="Programming language",
        ),
        "lines": PropertyConfig(
            name="lines",
            property_type=PropertyType.INTEGER,
            description="Total number of lines",
        ),
    },
    matching_rule=MatchingRule.EXACT,
    unique_key="path",
    description="A file in the codebase",
)

QUESTION_NODE = NodeTypeConfig(
    label="Question",
    properties={
        "text": PropertyConfig(
            name="text",
            property_type=PropertyType.STRING,
            required=True,
            description="The question text",
        ),
        "question_hash": PropertyConfig(
            name="question_hash",
            property_type=PropertyType.STRING,
            required=True,
            indexed=True,
            unique=True,
            description="SHA256 hash for fast lookup",
        ),
        "embedding": PropertyConfig(
            name="embedding",
            property_type=PropertyType.LIST_FLOAT,
            description="Vector embedding for similarity search",
        ),
        "created_at": PropertyConfig(
            name="created_at",
            property_type=PropertyType.DATETIME,
            description="When the question was first asked",
        ),
        "access_count": PropertyConfig(
            name="access_count",
            property_type=PropertyType.INTEGER,
            default=0,
            description="Number of times this question was accessed",
        ),
    },
    matching_rule=MatchingRule.SIMILARITY,
    embedding_property="embedding",
    unique_key="question_hash",
    similarity_threshold=0.7,
    description="A question that has been asked",
)

ANSWER_NODE = NodeTypeConfig(
    label="Answer",
    properties={
        "text": PropertyConfig(
            name="text",
            property_type=PropertyType.STRING,
            required=True,
            description="The answer text",
        ),
        "answer_id": PropertyConfig(
            name="answer_id",
            property_type=PropertyType.STRING,
            required=True,
            indexed=True,
            unique=True,
            description="Unique answer identifier",
        ),
        "answering_agent": PropertyConfig(
            name="answering_agent",
            property_type=PropertyType.STRING,
            description="Name of the agent that generated this answer",
        ),
        "answering_model": PropertyConfig(
            name="answering_model",
            property_type=PropertyType.STRING,
            description="Model used to generate this answer",
        ),
        "created_at": PropertyConfig(
            name="created_at",
            property_type=PropertyType.DATETIME,
            description="When the answer was created",
        ),
        "metadata": PropertyConfig(
            name="metadata",
            property_type=PropertyType.JSON,
            description="Additional metadata",
        ),
    },
    matching_rule=MatchingRule.EXACT,
    unique_key="answer_id",
    description="An answer to a question",
)

TOPIC_NODE = NodeTypeConfig(
    label="Topic",
    properties={
        "name": PropertyConfig(
            name="name",
            property_type=PropertyType.STRING,
            required=True,
            indexed=True,
            description="Topic name",
        ),
        "description": PropertyConfig(
            name="description",
            property_type=PropertyType.STRING,
            description="Description of the topic",
        ),
    },
    matching_rule=MatchingRule.EXACT,
    embedding_property=None,
    unique_key="name",
    description="A semantic topic or concept for categorization",
)

TEXT_NODE = NodeTypeConfig(
    label="Text",
    properties={
        "text": PropertyConfig(
            name="text",
            property_type=PropertyType.STRING,
            required=True,
            description="The text content",
        ),
        "text_hash": PropertyConfig(
            name="text_hash",
            property_type=PropertyType.STRING,
            required=True,
            indexed=True,
            unique=True,
            description="SHA256 hash for deduplication",
        ),
        "embedding": PropertyConfig(
            name="embedding",
            property_type=PropertyType.LIST_FLOAT,
            description="Vector embedding for similarity search",
        ),
        "source": PropertyConfig(
            name="source",
            property_type=PropertyType.STRING,
            description="Source of the text (e.g., 'memory_observer', 'tool_name')",
        ),
        "created_at": PropertyConfig(
            name="created_at",
            property_type=PropertyType.DATETIME,
            description="When the text was stored",
        ),
    },
    matching_rule=MatchingRule.SIMILARITY,
    embedding_property="embedding",
    unique_key="text_hash",
    similarity_threshold=0.7,
    description="Generic text content with semantic embedding",
)
