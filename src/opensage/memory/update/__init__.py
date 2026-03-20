"""Update interface for the memory system."""

from opensage.memory.update.entity_extractor import (
    EntityExtractor,
    ExtractedEntity,
    ExtractionResult,
)
from opensage.memory.update.graph_operations import (
    GraphOperations,
    OperationResult,
    OperationType,
)
from opensage.memory.update.operation_decider import LLMOperationDecider
from opensage.memory.update.relationship_discoverer import (
    DiscoveredRelationship,
    RelationshipDiscoverer,
)
from opensage.memory.update.update_controller import (
    MemoryUpdateController,
    UpdateResult,
)

__all__ = [
    "MemoryUpdateController",
    "UpdateResult",
    "EntityExtractor",
    "ExtractedEntity",
    "ExtractionResult",
    "RelationshipDiscoverer",
    "DiscoveredRelationship",
    "GraphOperations",
    "OperationType",
    "OperationResult",
    "LLMOperationDecider",
]
