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

        # Session-aware storage: {shared_session_id: {agent_id: agent/metadata}}
        self._session_agents: Dict[str, Dict[str, BaseAgent]] = {}
        self._session_metadata: Dict[str, Dict[str, AgentMetadata]] = {}
        self._global_hooks: List[AgentLifecycleHook] = []

    def add_lifecycle_hook(self, hook: AgentLifecycleHook) -> None:
        """Add a lifecycle hook."""
        self._global_hooks.append(hook)

    def remove_lifecycle_hook(self, hook: AgentLifecycleHook) -> None:
        """Remove a lifecycle hook."""
        if hook in self._global_hooks:
            self._global_hooks.remove(hook)

    def _get_shared_session_id_from_context(self, context) -> Optional[str]:
        """Extract shared_session_id from various context objects."""
        if context is None:
            return None

        # Try tool_context first
        if hasattr(context, "state") and hasattr(context.state, "get"):
            shared_session_id = context.state.get("shared_session_id")
            if shared_session_id:
                return shared_session_id

        # Try invocation_context
        if hasattr(context, "_invocation_context"):
            session = context._invocation_context.session
            if "shared_session_id" not in session.state:
                session.state["shared_session_id"] = session.id
            # Also set it in context.state if possible for immediate access
            if hasattr(context, "state"):
                context.state["shared_session_id"] = session.state["shared_session_id"]
            return session.state["shared_session_id"]

        # Try session directly
        if hasattr(context, "session"):
            session = context.session
            if "shared_session_id" not in session.state:
                session.state["shared_session_id"] = session.id
            # Also set it in context.state if possible for immediate access
            if hasattr(context, "state"):
                context.state["shared_session_id"] = session.state["shared_session_id"]
            return session.state["shared_session_id"]

        return None

    def _ensure_session_initialized(self, shared_session_id: str) -> None:
        """Ensure session storage structures exist."""
        if shared_session_id not in self._session_agents:
            self._session_agents[shared_session_id] = {}
        if shared_session_id not in self._session_metadata:
            self._session_metadata[shared_session_id] = {}

    async def create_agent(
        self,
        config: Dict[str, Any],
        creator: Optional[str] = None,
        persist: bool = True,
        shared_session_id: Optional[str] = None,
        context=None,
    ) -> tuple[str, BaseAgent]:
        """Create a new agent dynamically."""
        # Determine shared_session_id
        if shared_session_id is None:
            shared_session_id = self._get_shared_session_id_from_context(context)
            if shared_session_id is None:
                shared_session_id = "default"

        # Ensure session storage exists
        self._ensure_session_initialized(shared_session_id)

        agent_id = str(uuid.uuid4())

        try:
            # Extract basic config
            agent_type = config.get("type", "llm_agent")
            agent_name = config.get("name", f"agent_{agent_id[:8]}")

            agent_config = {
                k: v for k, v in config.items() if k not in ["type", "tool_names"]
            }
            agent = self.registry.create_agent(agent_type, **agent_config)

            metadata_config = {
                k: v for k, v in config.items() if k not in ["type", "tools"]
            }

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
                config=metadata_config,
            )

            # Store agent and metadata in session-specific storage
            self._session_agents[shared_session_id][agent_id] = agent
            self._session_metadata[shared_session_id][agent_id] = metadata

            if persist:
                await self._persist_agent(agent_id, shared_session_id)

            # Call lifecycle hooks
            for hook in self._global_hooks:
                try:
                    await hook.on_agent_created(agent, metadata)
                except Exception as e:
                    logger.error(f"Lifecycle hook error: {e}")

            logger.info(
                f"Created agent '{agent_name}' with ID: {agent_id} in session: {shared_session_id}"
            )
            return agent_id, agent

        except Exception as e:
            logger.error(f"Failed to create agent: {e}")

            # Create error metadata in session-specific storage
            if agent_id not in self._session_metadata.get(shared_session_id, {}):
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
                self._session_metadata[shared_session_id][agent_id] = error_metadata

            # Call error hooks
            for hook in self._global_hooks:
                try:
                    await hook.on_agent_error(
                        None, self._session_metadata[shared_session_id][agent_id], e
                    )
                except Exception as hook_error:
                    logger.error(f"Lifecycle hook error: {hook_error}")

            raise

    async def clone_agent(
        self,
        source_id: str,
        updates: Optional[Dict[str, Any]] = None,
        creator: Optional[str] = None,
        persist: bool = True,
        shared_session_id: Optional[str] = None,
        context=None,
    ) -> tuple[str, BaseAgent]:
        """Clone an existing agent with optional updates."""
        # Determine shared_session_id
        if shared_session_id is None:
            shared_session_id = self._get_shared_session_id_from_context(context)
            if shared_session_id is None:
                shared_session_id = "default"

        # Ensure session storage exists
        self._ensure_session_initialized(shared_session_id)

        # Find source agent in the same session
        if (
            shared_session_id not in self._session_agents
            or source_id not in self._session_agents[shared_session_id]
        ):
            raise ValueError(
                f"Agent {source_id} not found in session {shared_session_id}"
            )

        source_agent = self._session_agents[shared_session_id][source_id]
        source_metadata = self._session_metadata[shared_session_id][source_id]

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

        # Store cloned agent in session-specific storage
        self._session_agents[shared_session_id][new_id] = cloned_agent
        self._session_metadata[shared_session_id][new_id] = metadata

        # Update parent's children
        self._session_metadata[shared_session_id][source_id].children_ids.append(new_id)

        # Persist if requested
        if persist:
            await self._persist_agent(new_id, shared_session_id)
            await self._persist_agent(source_id, shared_session_id)  # Update parent

        logger.info(
            f"Cloned agent {source_id} to {new_id} in session: {shared_session_id}"
        )
        return new_id, cloned_agent

    async def update_agent_status(
        self,
        agent_id: str,
        status: AgentStatus,
        shared_session_id: Optional[str] = None,
        context=None,
    ) -> None:
        """Update agent status and call appropriate hooks."""
        if shared_session_id is None:
            shared_session_id = self._get_shared_session_id_from_context(context)
            if shared_session_id is None:
                shared_session_id = "default"

        if (
            shared_session_id not in self._session_metadata
            or agent_id not in self._session_metadata[shared_session_id]
        ):
            raise ValueError(
                f"Agent {agent_id} not found in session {shared_session_id}"
            )

        old_status = self._session_metadata[shared_session_id][agent_id].status
        self._session_metadata[shared_session_id][agent_id].status = status
        self._session_metadata[shared_session_id][agent_id].updated_at = datetime.now()

        agent = self._session_agents[shared_session_id].get(agent_id)
        metadata = self._session_metadata[shared_session_id][agent_id]

        # Call appropriate lifecycle hooks
        if status == AgentStatus.ACTIVE and old_status != AgentStatus.ACTIVE:
            for hook in self._global_hooks:
                try:
                    await hook.on_agent_started(agent, metadata)
                except Exception as e:
                    logger.error(f"Lifecycle hook error: {e}")

        elif status == AgentStatus.PAUSED and old_status != AgentStatus.PAUSED:
            for hook in self._global_hooks:
                try:
                    await hook.on_agent_paused(agent, metadata)
                except Exception as e:
                    logger.error(f"Lifecycle hook error: {e}")

        elif status == AgentStatus.STOPPED and old_status != AgentStatus.STOPPED:
            for hook in self._global_hooks:
                try:
                    await hook.on_agent_stopped(agent, metadata)
                except Exception as e:
                    logger.error(f"Lifecycle hook error: {e}")

        await self._persist_agent(agent_id, shared_session_id)

    def get_agent(
        self, agent_id: str, shared_session_id: Optional[str] = None, context=None
    ) -> Optional[BaseAgent]:
        """Get an agent by ID within a specific session."""
        if shared_session_id is None:
            shared_session_id = self._get_shared_session_id_from_context(context)
            if shared_session_id is None:
                shared_session_id = "default"

        return self._session_agents.get(shared_session_id, {}).get(agent_id)

    def get_agent_metadata(
        self, agent_id: str, shared_session_id: Optional[str] = None, context=None
    ) -> Optional[AgentMetadata]:
        """Get agent metadata by ID within a specific session."""
        if shared_session_id is None:
            shared_session_id = self._get_shared_session_id_from_context(context)
            if shared_session_id is None:
                shared_session_id = "default"

        return self._session_metadata.get(shared_session_id, {}).get(agent_id)

    def list_agents(
        self,
        status: Optional[AgentStatus] = None,
        creator: Optional[str] = None,
        shared_session_id: Optional[str] = None,
        context=None,
    ) -> List[AgentMetadata]:
        """List agents with optional filtering within a specific session."""
        if shared_session_id is None:
            shared_session_id = self._get_shared_session_id_from_context(context)
            if shared_session_id is None:
                shared_session_id = "default"

        session_metadata = self._session_metadata.get(shared_session_id, {})
        agents = list(session_metadata.values())

        if status:
            agents = [a for a in agents if a.status == status]

        if creator:
            agents = [a for a in agents if a.creator == creator]

        return agents

    async def remove_agent(
        self,
        agent_id: str,
        cascade: bool = False,
        shared_session_id: Optional[str] = None,
        context=None,
    ) -> bool:
        """Remove an agent from management within a specific session."""
        if shared_session_id is None:
            shared_session_id = self._get_shared_session_id_from_context(context)
            if shared_session_id is None:
                shared_session_id = "default"

        if (
            shared_session_id not in self._session_agents
            or agent_id not in self._session_agents[shared_session_id]
        ):
            return False

        metadata = self._session_metadata[shared_session_id][agent_id]

        # Handle children if cascade delete
        if cascade and metadata.children_ids:
            for child_id in metadata.children_ids.copy():
                await self.remove_agent(
                    child_id, cascade=True, shared_session_id=shared_session_id
                )

        # Update parent's children list
        if metadata.parent_id and metadata.parent_id in self._session_metadata.get(
            shared_session_id, {}
        ):
            parent_children = self._session_metadata[shared_session_id][
                metadata.parent_id
            ].children_ids
            if agent_id in parent_children:
                parent_children.remove(agent_id)
                await self._persist_agent(metadata.parent_id, shared_session_id)

        # Remove from memory
        del self._session_agents[shared_session_id][agent_id]
        del self._session_metadata[shared_session_id][agent_id]

        # Remove persistence
        metadata_file = (
            self.storage_path
            / "sessions"
            / shared_session_id
            / "metadata"
            / f"{agent_id}_metadata.json"
        )
        if metadata_file.exists():
            metadata_file.unlink()

        logger.info(f"Removed agent {agent_id} from session {shared_session_id}")
        return True

    async def _persist_agent(self, agent_id: str, shared_session_id: str) -> None:
        """Persist agent metadata to session-specific storage."""
        if (
            shared_session_id not in self._session_metadata
            or agent_id not in self._session_metadata[shared_session_id]
        ):
            return

        metadata = self._session_metadata[shared_session_id][agent_id]
        metadata_dict = asdict(metadata)

        # Convert datetime objects to ISO strings
        metadata_dict["created_at"] = metadata.created_at.isoformat()
        metadata_dict["updated_at"] = metadata.updated_at.isoformat()
        metadata_dict["status"] = metadata.status.value

        # Create session-specific directory structure
        session_path = self.storage_path / "sessions" / shared_session_id / "metadata"
        session_path.mkdir(parents=True, exist_ok=True)

        metadata_file = session_path / f"{agent_id}_metadata.json"
        with open(metadata_file, "w") as f:
            json.dump(metadata_dict, f, indent=2)

    def _load_persisted_agents_on_demand(self, caller_tools: Dict[str, Any]) -> None:
        """Load persisted agents on demand, rebuilding with caller tools if possible.

        Args:
            caller_tools: Dictionary mapping tool names to tool instances from caller agent
        """
        if not self.storage_path.exists():
            return

        for metadata_file in self.storage_path.glob("*_metadata.json"):
            try:
                with open(metadata_file, "r") as f:
                    metadata_dict = json.load(f)

                # Convert datetime strings back to datetime objects
                metadata_dict["created_at"] = datetime.fromisoformat(
                    metadata_dict["created_at"]
                )
                metadata_dict["updated_at"] = datetime.fromisoformat(
                    metadata_dict["updated_at"]
                )
                metadata_dict["status"] = AgentStatus(metadata_dict["status"])

                metadata = AgentMetadata(**metadata_dict)

                # Skip if agent already loaded
                if metadata.id in self._agents and metadata.id in self._metadata:
                    continue

                # Always store metadata
                self._metadata[metadata.id] = metadata

                # Try to rebuild agent if not in error state and has config
                if metadata.status != AgentStatus.ERROR and metadata.config:
                    self._try_rebuild_agent_with_caller_tools(metadata, caller_tools)

            except Exception as e:
                logger.error(f"Failed to load metadata from {metadata_file}: {e}")

    def _try_rebuild_agent_with_caller_tools(
        self, metadata: AgentMetadata, caller_tools: Dict[str, Any]
    ) -> None:
        """Try to rebuild an agent with tools from caller if all required tools are available.

        Args:
            metadata: Agent metadata containing config and tool requirements
            caller_tools: Available tools from caller agent
        """
        required_tool_names = metadata.config.get("tool_names", [])

        # If no tools required, create agent without tools
        if not required_tool_names:
            try:
                agent_config = {
                    k: v
                    for k, v in metadata.config.items()
                    if k not in ["type", "tool_names"]
                }
                agent_config["tools"] = []  # Explicitly set empty tools list
                agent = self.registry.create_agent(metadata.type, **agent_config)
                self._agents[metadata.id] = agent
                logger.info(f"Restored agent {metadata.id} without tools")
            except Exception as e:
                logger.error(f"Failed to restore agent {metadata.id}: {e}")
                metadata.status = AgentStatus.ERROR
            return

        # Check if caller can provide all required tools
        missing_tools = [
            name for name in required_tool_names if name not in caller_tools
        ]
        if missing_tools:
            logger.debug(
                f"Cannot restore agent {metadata.id}: missing tools {missing_tools}"
            )
            return

        # Rebuild agent with tools
        try:
            # Prepare tools from caller
            tools_to_assign = [caller_tools[name] for name in required_tool_names]

            # Prepare agent config including tools
            agent_config = {
                k: v
                for k, v in metadata.config.items()
                if k not in ["type", "tool_names"]
            }
            agent_config["tools"] = tools_to_assign

            # Create agent with tools directly
            agent = self.registry.create_agent(metadata.type, **agent_config)

            self._agents[metadata.id] = agent
            logger.info(
                f"Restored agent {metadata.id} with tools: {required_tool_names}"
            )

        except Exception as e:
            logger.error(f"Failed to restore agent {metadata.id}: {e}")
            metadata.status = AgentStatus.ERROR


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
