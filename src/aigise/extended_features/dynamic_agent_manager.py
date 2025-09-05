from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.llm_agent import LlmAgent

from .agent_registry import AgentRegistry, get_agent_registry

logger = logging.getLogger("aigise.extended_features." + __name__)


class AgentStatus(Enum):
    """Agent lifecycle status."""

    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class AgentMetadata:
    """Metadata for dynamically created agents."""

    id: str
    name: str
    type: str
    status: AgentStatus
    created_at: datetime
    updated_at: datetime
    creator: Optional[str] = None
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    parent_id: Optional[str] = None
    children_ids: List[str] = None

    def __post_init__(self):
        if self.children_ids is None:
            self.children_ids = []


class AgentLifecycleHook:
    """Hook interface for agent lifecycle events."""

    async def on_agent_created(self, agent: BaseAgent, metadata: AgentMetadata) -> None:
        """Called when an agent is created."""
        pass

    async def on_agent_started(self, agent: BaseAgent, metadata: AgentMetadata) -> None:
        """Called when an agent starts execution."""
        pass

    async def on_agent_paused(self, agent: BaseAgent, metadata: AgentMetadata) -> None:
        """Called when an agent is paused."""
        pass

    async def on_agent_stopped(self, agent: BaseAgent, metadata: AgentMetadata) -> None:
        """Called when an agent is stopped."""
        pass

    async def on_agent_error(
        self, agent: BaseAgent, metadata: AgentMetadata, error: Exception
    ) -> None:
        """Called when an agent encounters an error."""
        pass


class DynamicAgentManager:
    """Manager for dynamic agent creation, lifecycle, and persistence."""

    def __init__(
        self,
        registry: Optional[AgentRegistry] = None,
        storage_path: Optional[str] = None,
    ):
        self.registry = registry or get_agent_registry()
        self.storage_path = (
            Path(storage_path) if storage_path else Path("/tmp/agent_storage")
        )
        self.storage_path.mkdir(exist_ok=True)

        self._agents: Dict[str, BaseAgent] = {}
        self._metadata: Dict[str, AgentMetadata] = {}
        self._hooks: List[AgentLifecycleHook] = []

        # Load persisted agents
        self._load_persisted_agents()

    def add_lifecycle_hook(self, hook: AgentLifecycleHook) -> None:
        """Add a lifecycle hook."""
        self._hooks.append(hook)

    def remove_lifecycle_hook(self, hook: AgentLifecycleHook) -> None:
        """Remove a lifecycle hook."""
        if hook in self._hooks:
            self._hooks.remove(hook)

    async def create_agent(
        self,
        config: Dict[str, Any],
        creator: Optional[str] = None,
        persist: bool = True,
    ) -> tuple[str, BaseAgent]:
        """Create a new agent dynamically."""
        agent_id = str(uuid.uuid4())

        try:
            # Extract basic config
            agent_type = config.get("type", "llm_agent")
            agent_name = config.get("name", f"agent_{agent_id[:8]}")

            # Create agent using registry
            if "template" in config:
                template_name = config["template"]
                agent_config = {
                    k: v for k, v in config.items() if k not in ["type", "template"]
                }
                agent = self.registry.create_from_template(
                    template_name, **agent_config
                )
            else:
                agent_config = {k: v for k, v in config.items() if k != "type"}
                agent = self.registry.create_agent(agent_type, **agent_config)

            # Create metadata
            metadata = AgentMetadata(
                id=agent_id,
                name=agent_name,
                type=agent_type,
                status=AgentStatus.CREATED,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                creator=creator,
                description=config.get("description"),
                config=config,
            )

            # Store agent and metadata
            self._agents[agent_id] = agent
            self._metadata[agent_id] = metadata

            # Persist if requested
            if persist:
                await self._persist_agent(agent_id)

            # Call lifecycle hooks
            for hook in self._hooks:
                try:
                    await hook.on_agent_created(agent, metadata)
                except Exception as e:
                    logger.error(f"Lifecycle hook error: {e}")

            logger.info(f"Created agent '{agent_name}' with ID: {agent_id}")
            return agent_id, agent

        except Exception as e:
            logger.error(f"Failed to create agent: {e}")

            # Create error metadata
            if agent_id not in self._metadata:
                error_metadata = AgentMetadata(
                    id=agent_id,
                    name=config.get("name", f"agent_{agent_id[:8]}"),
                    type=config.get("type", "llm_agent"),
                    status=AgentStatus.ERROR,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                    creator=creator,
                    config=config,
                )
                self._metadata[agent_id] = error_metadata

            # Call error hooks
            for hook in self._hooks:
                try:
                    await hook.on_agent_error(None, self._metadata[agent_id], e)
                except Exception as hook_error:
                    logger.error(f"Lifecycle hook error: {hook_error}")

            raise

    async def clone_agent(
        self,
        source_id: str,
        updates: Optional[Dict[str, Any]] = None,
        creator: Optional[str] = None,
        persist: bool = True,
    ) -> tuple[str, BaseAgent]:
        """Clone an existing agent with optional updates."""
        if source_id not in self._agents:
            raise ValueError(f"Agent {source_id} not found")

        source_agent = self._agents[source_id]
        source_metadata = self._metadata[source_id]

        # Generate new ID and name
        new_id = str(uuid.uuid4())
        new_name = f"clone_{source_metadata.name}_{new_id[:8]}"

        # Prepare updates
        clone_updates = updates or {}
        if "name" not in clone_updates:
            clone_updates["name"] = new_name

        # Clone the agent
        cloned_agent = source_agent.clone(update=clone_updates)

        # Create new metadata
        new_config = source_metadata.config.copy() if source_metadata.config else {}
        new_config.update(clone_updates)

        metadata = AgentMetadata(
            id=new_id,
            name=clone_updates.get("name", new_name),
            type=source_metadata.type,
            status=AgentStatus.CREATED,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            creator=creator,
            description=clone_updates.get("description", source_metadata.description),
            config=new_config,
            parent_id=source_id,
        )

        # Store cloned agent
        self._agents[new_id] = cloned_agent
        self._metadata[new_id] = metadata

        # Update parent's children
        self._metadata[source_id].children_ids.append(new_id)

        # Persist if requested
        if persist:
            await self._persist_agent(new_id)
            await self._persist_agent(source_id)  # Update parent

        logger.info(f"Cloned agent {source_id} to {new_id}")
        return new_id, cloned_agent

    async def update_agent_status(self, agent_id: str, status: AgentStatus) -> None:
        """Update agent status and call appropriate hooks."""
        if agent_id not in self._metadata:
            raise ValueError(f"Agent {agent_id} not found")

        old_status = self._metadata[agent_id].status
        self._metadata[agent_id].status = status
        self._metadata[agent_id].updated_at = datetime.now()

        agent = self._agents.get(agent_id)
        metadata = self._metadata[agent_id]

        # Call appropriate lifecycle hooks
        if status == AgentStatus.ACTIVE and old_status != AgentStatus.ACTIVE:
            for hook in self._hooks:
                try:
                    await hook.on_agent_started(agent, metadata)
                except Exception as e:
                    logger.error(f"Lifecycle hook error: {e}")

        elif status == AgentStatus.PAUSED and old_status != AgentStatus.PAUSED:
            for hook in self._hooks:
                try:
                    await hook.on_agent_paused(agent, metadata)
                except Exception as e:
                    logger.error(f"Lifecycle hook error: {e}")

        elif status == AgentStatus.STOPPED and old_status != AgentStatus.STOPPED:
            for hook in self._hooks:
                try:
                    await hook.on_agent_stopped(agent, metadata)
                except Exception as e:
                    logger.error(f"Lifecycle hook error: {e}")

        await self._persist_agent(agent_id)

    def get_agent(self, agent_id: str) -> Optional[BaseAgent]:
        """Get an agent by ID."""
        return self._agents.get(agent_id)

    def get_agent_metadata(self, agent_id: str) -> Optional[AgentMetadata]:
        """Get agent metadata by ID."""
        return self._metadata.get(agent_id)

    def list_agents(
        self, status: Optional[AgentStatus] = None, creator: Optional[str] = None
    ) -> List[AgentMetadata]:
        """List agents with optional filtering."""
        agents = list(self._metadata.values())

        if status:
            agents = [a for a in agents if a.status == status]

        if creator:
            agents = [a for a in agents if a.creator == creator]

        return agents

    async def remove_agent(self, agent_id: str, cascade: bool = False) -> bool:
        """Remove an agent from management."""
        if agent_id not in self._agents:
            return False

        metadata = self._metadata[agent_id]

        # Handle children if cascade delete
        if cascade and metadata.children_ids:
            for child_id in metadata.children_ids.copy():
                await self.remove_agent(child_id, cascade=True)

        # Update parent's children list
        if metadata.parent_id and metadata.parent_id in self._metadata:
            parent_children = self._metadata[metadata.parent_id].children_ids
            if agent_id in parent_children:
                parent_children.remove(agent_id)
                await self._persist_agent(metadata.parent_id)

        # Remove from memory
        del self._agents[agent_id]
        del self._metadata[agent_id]

        # Remove persistence
        metadata_file = self.storage_path / f"{agent_id}_metadata.json"
        if metadata_file.exists():
            metadata_file.unlink()

        logger.info(f"Removed agent {agent_id}")
        return True

    async def _persist_agent(self, agent_id: str) -> None:
        """Persist agent metadata to storage."""
        if agent_id not in self._metadata:
            return

        metadata = self._metadata[agent_id]
        metadata_dict = asdict(metadata)

        # Convert datetime objects to ISO strings
        metadata_dict["created_at"] = metadata.created_at.isoformat()
        metadata_dict["updated_at"] = metadata.updated_at.isoformat()
        metadata_dict["status"] = metadata.status.value

        metadata_file = self.storage_path / f"{agent_id}_metadata.json"
        with open(metadata_file, "w") as f:
            json.dump(metadata_dict, f, indent=2)

    def _load_persisted_agents(self) -> None:
        """Load persisted agents from storage."""
        if not self.storage_path.exists():
            return

        for metadata_file in self.storage_path.glob("*_metadata.json"):
            try:
                with open(metadata_file, "r") as f:
                    metadata_dict = json.load(f)

                # Convert back to proper types
                metadata_dict["created_at"] = datetime.fromisoformat(
                    metadata_dict["created_at"]
                )
                metadata_dict["updated_at"] = datetime.fromisoformat(
                    metadata_dict["updated_at"]
                )
                metadata_dict["status"] = AgentStatus(metadata_dict["status"])

                metadata = AgentMetadata(**metadata_dict)

                # Try to recreate the agent if it's not in error state
                if metadata.status != AgentStatus.ERROR and metadata.config:
                    try:
                        agent_type = metadata.config.get("type", "llm_agent")
                        if "template" in metadata.config:
                            template_name = metadata.config["template"]
                            agent_config = {
                                k: v
                                for k, v in metadata.config.items()
                                if k not in ["type", "template"]
                            }
                            agent = self.registry.create_from_template(
                                template_name, **agent_config
                            )
                        else:
                            agent_config = {
                                k: v for k, v in metadata.config.items() if k != "type"
                            }
                            agent = self.registry.create_agent(
                                agent_type, **agent_config
                            )

                        self._agents[metadata.id] = agent
                        self._metadata[metadata.id] = metadata

                        logger.info(f"Restored agent {metadata.id}")

                    except Exception as e:
                        logger.error(f"Failed to restore agent {metadata.id}: {e}")
                        metadata.status = AgentStatus.ERROR
                        self._metadata[metadata.id] = metadata
                else:
                    # Just store metadata for error/stopped agents
                    self._metadata[metadata.id] = metadata

            except Exception as e:
                logger.error(f"Failed to load metadata from {metadata_file}: {e}")


# Global manager instance
_global_manager: Optional[DynamicAgentManager] = None


def get_dynamic_agent_manager() -> DynamicAgentManager:
    """Get the global dynamic agent manager instance."""
    global _global_manager
    if _global_manager is None:
        _global_manager = DynamicAgentManager()
    return _global_manager


def set_dynamic_agent_manager(manager: DynamicAgentManager) -> None:
    """Set the global dynamic agent manager instance."""
    global _global_manager
    _global_manager = manager
