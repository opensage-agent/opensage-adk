"""Update interface for the memory system."""

from opensage.memory.database_based.long_term.update.entity_extractor import (
    EntityExtractor,
    ExtractedEntity,
    ExtractionResult,
)
from opensage.memory.database_based.long_term.update.graph_operations import (
    GraphOperations,
    OperationResult,
    OperationType,
)
from opensage.memory.database_based.long_term.update.operation_decider import (
    LLMOperationDecider,
)
from opensage.memory.database_based.long_term.update.relationship_discoverer import (
    DiscoveredRelationship,
    RelationshipDiscoverer,
)
from opensage.memory.database_based.long_term.update.update_controller import (
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
