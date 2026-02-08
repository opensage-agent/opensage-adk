"""Update interface for the memory system."""

from aigise.memory.update.entity_extractor import (
    EntityExtractor,
    ExtractedEntity,
    ExtractionResult,
)
from aigise.memory.update.graph_operations import (
    GraphOperations,
    OperationResult,
    OperationType,
)
from aigise.memory.update.operation_decider import LLMOperationDecider
from aigise.memory.update.relationship_discoverer import (
    DiscoveredRelationship,
    RelationshipDiscoverer,
)
from aigise.memory.update.update_controller import MemoryUpdateController, UpdateResult

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
