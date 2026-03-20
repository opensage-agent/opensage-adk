"""LLM-based operation decision for memory updates."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from opensage.memory.config.memory_settings import get_memory_settings
from opensage.memory.update.entity_extractor import ExtractedEntity
from opensage.memory.update.graph_operations import OperationType

logger = logging.getLogger(__name__)


class LLMOperationDecider:
    """Uses LLM to decide what operation to perform on entities.

    When storing new knowledge, this class helps determine whether to:
    - ADD: Create a new entity (information is genuinely new)
    - UPDATE: Modify an existing entity (better/more recent information)
    - DELETE: Remove an existing entity (outdated/incorrect information)
    - NONE: Skip the operation (duplicate information already exists)
    """

    def __init__(self, model: Optional[str] = None):
        """Initialize the operation decider.

        Args:
            model (Optional[str]): The LLM model to use for decisions. If None, uses settings."""
        self.model = model or get_memory_settings().llm_model

    async def decide_operation(
        self,
        entity: ExtractedEntity,
        existing_nodes: List[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> OperationType:
        """Decide whether to ADD, UPDATE, DELETE, or do NONE.

        Args:
            entity (ExtractedEntity): The extracted entity to consider storing.
            existing_nodes (List[Dict[str, Any]]): Similar/matching nodes already in the graph.
            context (Dict[str, Any]): Additional context with required 'intent' key.
        Returns:
            OperationType: OperationType indicating what action to take.

        Raises:
            ValueError: If context is None or missing 'intent'.
        """
        # Require context with intent
        if not context or "intent" not in context:
            raise ValueError("context with 'intent' is required for decide_operation")

        # No existing nodes -> ADD (fast path)
        if not existing_nodes:
            logger.info(
                f"[OperationDecider] No existing nodes for {entity.label}, returning ADD"
            )
            return OperationType.ADD

        try:
            import litellm

            # Build summary of existing nodes
            existing_summary = []
            for node in existing_nodes[:3]:
                text = node.get("text", node.get("name", ""))
                if len(text) > 100:
                    text = text[:100] + "..."
                existing_summary.append(f"- {node.get('label', 'Unknown')}: {text}")

            # Get entity content for comparison
            entity_content = entity.properties.get(
                "text", entity.properties.get("name", str(entity.properties))
            )
            if len(entity_content) > 200:
                entity_content = entity_content[:200] + "..."

            intent = context["intent"]

            prompt = f"""Decide what operation to perform for this entity.

New Entity:
- Type: {entity.label}
- Content: {entity_content}

Existing similar entities in memory:
{chr(10).join(existing_summary) if existing_summary else "None"}

Context: {intent}

Choose one operation:
- ADD: If this is genuinely new information not covered by existing entries
- UPDATE: If this should replace/update an existing entry with better/more recent information
- DELETE: If the new information indicates an existing entry is outdated/incorrect
- NONE: If the information already exists and doesn't need any changes

Respond with only the operation name (ADD, UPDATE, DELETE, or NONE)."""

            response = await litellm.acompletion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=1,
                max_tokens=10,
            )
            result = response.choices[0].message.content.strip().upper()

            # Parse the result
            if result == "ADD":
                logger.info(f"[OperationDecider] LLM decided ADD for {entity.label}")
                return OperationType.ADD
            elif result == "UPDATE":
                logger.info(f"[OperationDecider] LLM decided UPDATE for {entity.label}")
                return OperationType.UPDATE
            elif result == "DELETE":
                logger.info(f"[OperationDecider] LLM decided DELETE for {entity.label}")
                return OperationType.DELETE
            elif result == "NONE":
                logger.info(f"[OperationDecider] LLM decided NONE for {entity.label}")
                return OperationType.NONE
            else:
                # If we can't parse the result, default to ADD for new entities
                logger.warning(
                    f"[OperationDecider] Could not parse LLM response '{result}' "
                    f"for {entity.label}, defaulting to ADD. "
                    f"Entity content: {entity_content[:100]}"
                )
                return OperationType.ADD

        except Exception as e:
            logger.warning(
                f"[OperationDecider] LLM operation decision failed for {entity.label}: {e}. "
                f"Defaulting to ADD. Entity: {entity.properties}"
            )
            # Default to ADD for new entities when LLM fails
            return OperationType.ADD

    async def decide_operations_batch(
        self,
        entities: List[ExtractedEntity],
        existing_nodes_map: Dict[str, List[Dict[str, Any]]],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, OperationType]:
        """Decide operations for multiple entities.

        Args:
            entities (List[ExtractedEntity]): List of extracted entities.
            existing_nodes_map (Dict[str, List[Dict[str, Any]]]): Map from entity identifier to existing similar nodes.
            context (Optional[Dict[str, Any]]): Additional context.
        Returns:
            Dict[str, OperationType]: Dictionary mapping entity identifier to operation type.
        """
        results = {}
        for entity in entities:
            # Use a unique key for the entity
            key = f"{entity.label}:{entity.properties.get('name', entity.properties.get('question_hash', id(entity)))}"
            existing = existing_nodes_map.get(key, [])
            operation = await self.decide_operation(entity, existing, context)
            results[key] = operation
        return results
