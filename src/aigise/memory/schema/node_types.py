"""Node type configurations for the memory graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class MatchingRule(Enum):
    """How to match nodes when creating relationships."""

    EXACT = "exact"
    """Match only on exact property value equality."""

    SIMILARITY = "similarity"
    """Match based on embedding similarity above threshold."""

    HYBRID = "hybrid"
    """Try exact match first, fall back to similarity."""


class PropertyType(Enum):
    """Property data types supported by the memory system."""

    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    LIST_STRING = "list_string"
    LIST_FLOAT = "list_float"  # For embeddings
    JSON = "json"


@dataclass
class PropertyConfig:
    """Configuration for a single property on a node type."""

    name: str
    """Property name as it appears in Neo4j."""

    property_type: PropertyType
    """Data type of the property."""

    required: bool = False
    """Whether this property must be set when creating nodes."""

    indexed: bool = False
    """Whether to create a standard index on this property."""

    unique: bool = False
    """Whether values must be unique across all nodes of this type."""

    default: Optional[Any] = None
    """Default value if not provided during creation."""

    description: str = ""
    """Human-readable description of the property's purpose."""


@dataclass
class NodeTypeConfig:
    """Configuration for a node type in the memory graph.

    This defines how a particular type of entity is stored and matched
    in the Neo4j graph. Each node type has:
    - A Neo4j label
    - A set of properties with their configurations
    - A matching rule for relationship discovery
    - Optional embedding property for similarity search
    """

    label: str
    """Neo4j node label (e.g., 'Function', 'Question', 'Topic')."""

    properties: Dict[str, PropertyConfig] = field(default_factory=dict)
    """Property configurations keyed by property name."""

    matching_rule: MatchingRule = MatchingRule.EXACT
    """How to match this node type when discovering relationships."""

    embedding_property: Optional[str] = None
    """Name of the property containing vector embeddings (if any)."""

    embedding_dimension: int = 3072  # TODO can we make this configuraable?
    """Dimension of the embedding vectors (default: Gemini's 3072)."""

    unique_key: Optional[str] = None
    """Property used as unique identifier for MERGE operations."""

    composite_unique_keys: Optional[List[str]] = None
    """List of properties that together form a unique identifier."""

    similarity_threshold: float = 0.7
    """Minimum similarity score for SIMILARITY matching (0.0 to 1.0)."""

    description: str = ""
    """Human-readable description of this node type."""

    def __post_init__(self):
        """Validate configuration after initialization.

        Raises:
          ValueError: Raised when this operation fails."""
        # Ensure embedding_property exists in properties if specified
        if self.embedding_property and self.embedding_property not in self.properties:
            self.properties[self.embedding_property] = PropertyConfig(
                name=self.embedding_property,
                property_type=PropertyType.LIST_FLOAT,
                description="Vector embedding for similarity search",
            )

        # Ensure unique_key exists in properties if specified
        if self.unique_key and self.unique_key not in self.properties:
            raise ValueError(
                f"unique_key '{self.unique_key}' not found in properties for {self.label}"
            )

        # Validate composite unique keys
        if self.composite_unique_keys:
            for key in self.composite_unique_keys:
                if key not in self.properties:
                    raise ValueError(
                        f"composite_unique_key '{key}' not found in properties for {self.label}"
                    )

    def get_property_names(self) -> List[str]:
        """Get all property names for this node type."""
        return list(self.properties.keys())

    def get_required_properties(self) -> List[str]:
        """Get names of required properties."""
        return [name for name, config in self.properties.items() if config.required]

    def get_indexed_properties(self) -> List[str]:
        """Get names of indexed properties."""
        return [name for name, config in self.properties.items() if config.indexed]

    def supports_similarity_search(self) -> bool:
        """Check if this node type supports similarity search."""
        return self.embedding_property is not None and self.matching_rule in (
            MatchingRule.SIMILARITY,
            MatchingRule.HYBRID,
        )

    def get_merge_keys(self) -> List[str]:
        """Get the key(s) used for MERGE operations."""
        if self.composite_unique_keys:
            return self.composite_unique_keys
        elif self.unique_key:
            return [self.unique_key]
        else:
            # Fall back to required properties
            return self.get_required_properties()


# Pre-defined common node types

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
    matching_rule=MatchingRule.EXACT,  # Topics are for categorization, not similarity search
    embedding_property=None,  # Topics don't have embeddings
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
