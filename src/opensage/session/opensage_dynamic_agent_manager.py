"""
DynamicAgentManager: Session-specific agent lifecycle management

This module provides session-bound agent management, replacing the global
DynamicAgentManager with session-isolated agent handling.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from google.adk.agents.base_agent import BaseAgent
from google.adk.models.lite_llm import LiteLlm

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.utils.agent_utils import INHERIT_MODEL

logger = logging.getLogger(__name__)

# TODO: Do we need a get_session_statistics function here?


class AgentStatus(Enum):
    """Agent lifecycle status."""

    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"
    PENDING_TOOLS = "pending_tools"  # Waiting for required tools to be available


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


class DynamicAgentManager:
    """Session-specific manager for dynamic agent creation, lifecycle, and persistence.

    Each OpenSageSession gets its own DynamicAgentManager instance,
    ensuring complete agent isolation between sessions.
    """

    def __init__(self, session):
        """Initialize DynamicAgentManager.

        Args:
            session: OpenSageSession instance (stores reference, not copied)"""
        self._session = session
        self.opensage_session_id = session.opensage_session_id

        # Get storage path from config, use default if not specified or empty
        storage_path = session.config.agent_storage_path
        if not storage_path:  # None or empty string
            storage_path = "~/.local/opensage/dynamic_agents"

        self.storage_path = Path(storage_path).expanduser()
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._agents: Dict[str, BaseAgent] = {}
        self._metadata: Dict[str, AgentMetadata] = {}

    @property
    def config(self):
        """Get latest config from session dynamically."""
        return self._session.config

    def _create_agent_instance(self, **kwargs) -> OpenSageAgent:
        """Create an OpenSageAgent instance.

        Args:
            **kwargs: Agent configuration parameters
        Returns:
            OpenSageAgent: Created OpenSageAgent instance

        Raises:
            ValueError: If required parameters are missing
        """
        # Handle special model inheritance and common model string wrapping.
        #
        # NOTE: Dynamic agents are persisted; we keep "model" as a string in the
        # persisted metadata. For INHERIT_MODEL we optionally accept a
        # non-persisted "_resolved_model" (BaseLlm) at creation time. When
        # reloading from disk, fall back to the session's main model.
        resolved_model = kwargs.pop("_resolved_model", None)
        if "model" in kwargs and isinstance(kwargs["model"], str):
            if kwargs["model"] == INHERIT_MODEL:
                if resolved_model is not None:
                    kwargs["model"] = resolved_model
                else:
                    fallback = getattr(self.config.llm, "model_name", None)
                    if not fallback:
                        raise ValueError(
                            "model='inherit' requires either _resolved_model "
                            "or a configured llm.model_configs.main.model_name"
                        )
                    kwargs["model"] = LiteLlm(model=fallback)
            else:
                kwargs["model"] = LiteLlm(model=kwargs["model"])

        # Validate required parameters
        required_params = ["name", "model"]
        missing = [param for param in required_params if param not in kwargs]
        if missing:
            raise ValueError(f"Missing required parameters: {missing}")

        # Create OpenSageAgent
        agent = OpenSageAgent(**kwargs)

        return agent

    async def create_agent(
        self,
        config: Dict[str, Any],
        creator: Optional[str] = None,
        persist: bool = True,
    ) -> tuple[str, OpenSageAgent]:
        """Create a new agent dynamically for this session.

                Args:
                    config (Dict[str, Any]): Agent configuration dictionary
                    creator (Optional[str]): Optional creator identifier
                    persist (bool): Whether to persist agent metadata

        Raises:
          Exception: Raised when this operation fails.
                Returns:
                    tuple[str, OpenSageAgent]: Tuple of (agent_id, agent_instance)
        """
        agent_id = str(uuid.uuid4())

        try:
            agent_name = config.get("name", f"agent_{agent_id[:8]}")

            # Create agent configuration (exclude tool_names)
            agent_config = {k: v for k, v in config.items() if k not in ["tool_names"]}
            agent = self._create_agent_instance(**agent_config)

            # Create metadata (exclude tools and private runtime fields - cannot be serialized).
            # Note: enabled_skills is included in metadata_config for persistence.
            metadata_config = {
                k: v
                for k, v in config.items()
                if k not in ["tools"] and not str(k).startswith("_")
            }

            # Verify enabled_skills is included in metadata_config
            if "enabled_skills" in config:
                if "enabled_skills" not in metadata_config:
                    logger.warning(
                        f"enabled_skills was in config but not in metadata_config for agent {agent_id}"
                    )
                else:
                    logger.debug(
                        f"Storing enabled_skills={metadata_config['enabled_skills']} "
                        f"for agent {agent_id}"
                    )

            metadata = AgentMetadata(
                id=agent_id,
                name=agent_name,
                type="opensage_agent",
                status=AgentStatus.CREATED,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                creator=creator,
                config=metadata_config,
            )

            # Store agent and metadata
            self._agents[agent_id] = agent
            self._metadata[agent_id] = metadata

            # Persist if requested
            if persist:
                self._persist_agent_metadata(agent_id, metadata)

            logger.info(
                f"Created OpenSageAgent {agent_id} ({agent_name}) "
                f"for session {self.opensage_session_id}"
            )

            return agent_id, agent

        except Exception as e:
            logger.error(f"Failed to create agent: {e}")
            raise

    def get_agent(self, agent_id: str) -> Optional[BaseAgent]:
        """Get an agent by ID for this session."""
        return self._agents.get(agent_id)

    def get_agent_metadata(self, agent_id: str) -> Optional[AgentMetadata]:
        """Get agent metadata by ID for this session."""
        return self._metadata.get(agent_id)

    async def update_agent_status(self, agent_id: str, status: AgentStatus) -> bool:
        """Update agent status and trigger lifecycle hooks.

        Args:
            agent_id (str): ID of the agent to update
            status (AgentStatus): New status to set
        Returns:
            bool: True if updated successfully, False if agent not found
        """
        if agent_id not in self._metadata:
            return False

        old_status = self._metadata[agent_id].status
        self._metadata[agent_id].status = status
        self._metadata[agent_id].updated_at = datetime.now()

        # Persist changes
        self._persist_agent_metadata(agent_id, self._metadata[agent_id])

        logger.info(f"Updated agent {agent_id} status: {old_status} -> {status}")
        return True

    def list_agents(
        self,
        status: Optional[AgentStatus] = None,
        creator: Optional[str] = None,
    ) -> List[AgentMetadata]:
        """List agents with optional filtering for this session.

        Args:
            status (Optional[AgentStatus]): Optional status filter
            creator (Optional[str]): Optional creator filter
        Returns:
            List[AgentMetadata]: List of agent metadata matching the filters
        """
        agents = list(self._metadata.values())

        if status:
            agents = [a for a in agents if a.status == status]

        if creator:
            agents = [a for a in agents if a.creator == creator]

        return agents

    async def remove_agent(
        self,
        agent_id: str,
        cascade: bool = False,
    ) -> bool:
        """Remove an agent from this session.

        Args:
            agent_id (str): ID of the agent to remove
            cascade (bool): Whether to remove child agents as well
        Returns:
            bool: True if removed successfully, False if not found
        """
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
                self._persist_agent_metadata(
                    metadata.parent_id, self._metadata[metadata.parent_id]
                )

        # Remove from memory
        del self._agents[agent_id]
        del self._metadata[agent_id]

        # Remove persistence
        metadata_file = self.storage_path / f"{agent_id}_metadata.json"
        if metadata_file.exists():
            metadata_file.unlink()

        logger.info(f"Removed agent {agent_id} from session {self.opensage_session_id}")
        return True

    def get_session_statistics(self) -> Dict:
        """Get statistics for this session's agents.

        Returns:
            Dict: Dictionary with session statistics
        """
        status_counts = {}
        for metadata in self._metadata.values():
            status = metadata.status.value
            status_counts[status] = status_counts.get(status, 0) + 1

        return {
            "opensage_session_id": self.opensage_session_id,
            "total_agents": len(self._agents),
            "status_counts": status_counts,
        }

    def _persist_agent_metadata(self, agent_id: str, metadata: AgentMetadata) -> None:
        """Persist agent metadata to storage.

        Args:
            agent_id (str): ID of the agent
            metadata (AgentMetadata): Metadata to persist"""
        metadata_dict = asdict(metadata)

        # Convert datetime objects to ISO strings
        metadata_dict["created_at"] = metadata.created_at.isoformat()
        metadata_dict["updated_at"] = metadata.updated_at.isoformat()
        metadata_dict["status"] = metadata.status.value

        metadata_file = self.storage_path / f"{agent_id}_metadata.json"
        with open(metadata_file, "w") as f:
            json.dump(metadata_dict, f, indent=2)

    def _load_persisted_agents_on_demand(
        self, caller_tools: Dict[str, Any], caller_agent: Optional[BaseAgent] = None
    ) -> None:
        """Load persisted agents on demand, rebuilding with caller tools if possible.

        Args:
            caller_tools (Dict[str, Any]): Dictionary mapping tool names to tool instances from caller agent
            caller_agent (Optional[BaseAgent]): Optional caller agent instance (for enabled_skills check)"""
        if not getattr(self.config, "load_dynamic_agents", False):
            return

        if not self.storage_path.exists():
            return

        # for metadata_file in self.storage_path.glob("*_metadata.json"):
        #     try:
        #         # Extract agent ID from filename and check if already loaded (optimization)
        #         filename = metadata_file.name
        #         if filename.endswith("_metadata.json"):
        #             agent_id = filename[: -len("_metadata.json")]

        #             # Skip if agent already loaded - avoid file I/O
        #             if agent_id in self._agents and agent_id in self._metadata:
        #                 continue

        #         # Only read and parse file if agent is not already loaded
        #         with open(metadata_file, "r") as f:
        #             metadata_dict = json.load(f)

        #         # Convert datetime strings back to datetime objects
        #         metadata_dict["created_at"] = datetime.fromisoformat(
        #             metadata_dict["created_at"]
        #         )
        #         metadata_dict["updated_at"] = datetime.fromisoformat(
        #             metadata_dict["updated_at"]
        #         )
        #         metadata_dict["status"] = AgentStatus(metadata_dict["status"])

        #         metadata = AgentMetadata(**metadata_dict)

        #         # Always store metadata
        #         self._metadata[metadata.id] = metadata

        #         # Try to rebuild agent if not in permanent error state and has config
        #         # Allow retry for PENDING_TOOLS status, but not for ERROR status
        #         if metadata.status != AgentStatus.ERROR and metadata.config:
        #             self._try_rebuild_agent_with_caller_tools(metadata, caller_tools)

        #     except Exception as e:
        #         logger.error(f"Failed to load metadata from {metadata_file}: {e}")

    def _check_enabled_skills_coverage(
        self,
        agent_required_skills: Optional[Union[List[str], str]],
        caller_skills: Optional[Union[List[str], str]],
    ) -> bool:
        """Check if caller's enabled_skills covers agent's required enabled_skills.

        Supports prefix matching: e.g., caller's "fuzz" covers agent's
        "fuzz/simplified-python-fuzzer".

        Args:
            agent_required_skills (Optional[Union[List[str], str]]): Agent's required enabled_skills (None, "all", or List[str])
            caller_skills (Optional[Union[List[str], str]]): Caller's enabled_skills (None, "all", or List[str])
        Returns:
            bool: True if caller covers agent's requirements, False otherwise
        """
        # Case 1: Agent requires no tools
        if agent_required_skills is None:
            return True  # Caller can always cover (agent needs nothing)

        # Case 2: Agent requires all tools
        if agent_required_skills == "all":
            # Only caller with "all" can cover
            return caller_skills == "all"

        # Case 3: Agent requires specific tools (List[str])
        if isinstance(agent_required_skills, list):
            agent_set = set(agent_required_skills)

            # If caller has "all", it covers everything
            if caller_skills == "all":
                return True

            # If caller has None, it covers nothing
            if caller_skills is None:
                return False

            # If caller has a list, check if it covers all agent's required tools
            if isinstance(caller_skills, list):
                caller_set = set(caller_skills)

                # Check each agent required skill
                for agent_skill in agent_set:
                    # Check exact match or prefix match
                    # e.g., caller has "fuzz" and agent needs "fuzz/simplified-python-fuzzer"
                    # or caller has "fuzz/simplified-python-fuzzer" and agent needs "fuzz/simplified-python-fuzzer"
                    covered = False
                    for caller_skill in caller_set:
                        # Exact match
                        if agent_skill == caller_skill:
                            covered = True
                            break
                        # Prefix match: caller_skill is a prefix of agent_skill
                        # e.g., "fuzz" covers "fuzz/simplified-python-fuzzer"
                        if agent_skill.startswith(caller_skill + "/"):
                            covered = True
                            break
                    if not covered:
                        return False

                return True

        # Should not reach here, but return False for safety
        return False

    def _try_rebuild_agent_with_caller_tools(
        self,
        metadata: AgentMetadata,
        caller_tools: Dict[str, Any],
        caller_agent: Optional[BaseAgent] = None,
    ) -> None:
        """Try to rebuild an agent with tools from caller if all required tools are available.

        Args:
            metadata (AgentMetadata): Agent metadata containing config and tool requirements
            caller_tools (Dict[str, Any]): Available tools from caller agent
            caller_agent (Optional[BaseAgent]): Optional caller agent instance (for enabled_skills check)"""
        required_tool_names = metadata.config.get("tool_names", [])
        agent_required_skills = (
            metadata.config.get("enabled_skills") if metadata.config else None
        )

        # Check enabled_skills coverage if both agent and caller have enabled_skills
        if agent_required_skills is not None and caller_agent is not None:
            caller_skills = getattr(caller_agent, "_enabled_skills", None)
            if not self._check_enabled_skills_coverage(
                agent_required_skills, caller_skills
            ):
                logger.debug(
                    f"Cannot restore agent {metadata.id}: caller's enabled_skills "
                    f"({caller_skills}) does not cover agent's required enabled_skills "
                    f"({agent_required_skills})"
                )
                metadata.status = AgentStatus.PENDING_TOOLS
                metadata.updated_at = datetime.now()
                return

        # If no tools required, create agent without tools
        if not required_tool_names:
            try:
                agent_config = {
                    k: v for k, v in metadata.config.items() if k not in ["tool_names"]
                }
                agent_config["tools"] = []  # Explicitly set empty tools list
                # Ensure enabled_skills is passed
                if agent_required_skills is not None:
                    agent_config["enabled_skills"] = agent_required_skills
                agent = self._create_agent_instance(**agent_config)
                self._agents[metadata.id] = agent
                # Set to ACTIVE status on successful rebuild (clear any previous error states)
                metadata.status = AgentStatus.ACTIVE
                metadata.updated_at = datetime.now()
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
            # Set PENDING_TOOLS status (not ERROR) - this is a temporary tool availability issue
            metadata.status = AgentStatus.PENDING_TOOLS
            metadata.updated_at = datetime.now()
            return

        # Rebuild agent with tools
        try:
            # Prepare tools from caller
            tools_to_assign = [caller_tools[name] for name in required_tool_names]

            # Prepare agent config including tools
            agent_config = {
                k: v for k, v in metadata.config.items() if k not in ["tool_names"]
            }
            agent_config["tools"] = tools_to_assign
            # Ensure enabled_skills is passed
            if agent_required_skills is not None:
                agent_config["enabled_skills"] = agent_required_skills

            # Create agent with tools directly
            agent = self._create_agent_instance(**agent_config)

            self._agents[metadata.id] = agent
            # Set to ACTIVE status on successful rebuild (clear any previous error states)
            metadata.status = AgentStatus.ACTIVE
            metadata.updated_at = datetime.now()
            logger.info(
                f"Restored agent {metadata.id} with tools: {required_tool_names}"
            )

        except Exception as e:
            logger.error(f"Failed to restore agent {metadata.id}: {e}")
            metadata.status = AgentStatus.ERROR

    def cleanup(self) -> None:
        """Cleanup all agents and resources for this session."""
        logger.info(
            f"Cleaning up DynamicAgentManager for session {self.opensage_session_id}"
        )

        # Clear all agents and metadata
        self._agents.clear()
        self._metadata.clear()
