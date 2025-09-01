from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from .agent_registry import get_agent_registry
from .dynamic_agent_manager import AgentStatus, get_dynamic_agent_manager

logger = logging.getLogger('aigise.extended_features.' + __name__)


class CreateAgentTool(BaseTool):
    """Tool for creating new agents dynamically."""

    def __init__(self):
        super().__init__(
            name="create_agent",
            description="""Create a new agent dynamically with specified configuration.

            Use this tool to create specialized agents for specific tasks during runtime.
            The created agent can then be used as a sub-agent or for delegation.""",
        )

    async def run_async(
        self, *, args: Dict[str, Any], tool_context: ToolContext
    ) -> Dict[str, Any]:
        """Create a new agent.

        Args:
            args: Dictionary containing agent creation parameters
            tool_context: The tool execution context

        Returns:
            Dictionary with agent creation result
        """
        try:
            manager = get_dynamic_agent_manager()

            # Extract parameters from args
            agent_type = args.get("agent_type", "llm_agent")
            name = args.get("name")
            description = args.get("description")
            instruction = args.get("instruction")
            model = args.get("model")
            tools = args.get("tools")
            template = args.get("template")
            config = args.get("config")
            persist = args.get("persist", True)

            # Build configuration
            agent_config = {"type": agent_type}

            if template:
                agent_config["template"] = template

            if name:
                agent_config["name"] = name

            if description:
                agent_config["description"] = description

            if instruction:
                agent_config["instruction"] = instruction

            if model:
                agent_config["model"] = model

            if tools:
                agent_config["tools"] = tools

            # Parse additional config if provided
            if config:
                try:
                    additional_config = json.loads(config)
                    agent_config.update(additional_config)
                except json.JSONDecodeError as e:
                    return {
                        "success": False,
                        "error": f"Invalid JSON in config: {e}",
                        "agent_id": None,
                    }

            # Get creator from context
            creator = getattr(tool_context, 'user_id', None) or "system"

            # Create the agent
            agent_id, agent = await manager.create_agent(
                config=agent_config, creator=creator, persist=persist
            )

            # Update status to active
            await manager.update_agent_status(agent_id, AgentStatus.ACTIVE)

            return {
                "success": True,
                "agent_id": agent_id,
                "agent_name": agent.name,
                "agent_description": agent.description,
                "message": f"Successfully created agent '{agent.name}' with ID: {agent_id}",
            }

        except Exception as e:
            logger.error(f"Failed to create agent: {e}")
            return {"success": False, "error": str(e), "agent_id": None}


class CloneAgentTool(BaseTool):
    """Tool for cloning existing agents."""

    def __init__(self):
        super().__init__(
            name="clone_agent",
            description="""Clone an existing agent with optional modifications.

            This tool creates a copy of an existing agent, allowing you to modify
            specific properties while keeping the rest of the configuration.""",
        )

    async def call(
        self,
        tool_context: ToolContext,
        source_agent_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        instruction: Optional[str] = None,
        model: Optional[str] = None,
        updates: Optional[str] = None,
        persist: bool = True,
    ) -> Dict[str, Any]:
        """Clone an existing agent.

        Args:
            tool_context: The tool execution context
            source_agent_id: ID of the agent to clone
            name: New name for the cloned agent
            description: New description for the cloned agent
            instruction: New instruction for the cloned agent
            model: New model for the cloned agent
            updates: JSON string with additional updates
            persist: Whether to persist the cloned agent

        Returns:
            Dictionary with clone result
        """
        try:
            manager = get_dynamic_agent_manager()

            # Build updates
            clone_updates = {}

            if name:
                clone_updates["name"] = name

            if description:
                clone_updates["description"] = description

            if instruction:
                clone_updates["instruction"] = instruction

            if model:
                clone_updates["model"] = model

            # Parse additional updates
            if updates:
                try:
                    additional_updates = json.loads(updates)
                    clone_updates.update(additional_updates)
                except json.JSONDecodeError as e:
                    return {
                        "success": False,
                        "error": f"Invalid JSON in updates: {e}",
                        "agent_id": None,
                    }

            # Get creator from context
            creator = getattr(tool_context, 'user_id', None) or "system"

            # Clone the agent
            new_agent_id, new_agent = await manager.clone_agent(
                source_id=source_agent_id,
                updates=clone_updates,
                creator=creator,
                persist=persist,
            )

            # Update status to active
            await manager.update_agent_status(new_agent_id, AgentStatus.ACTIVE)

            return {
                "success": True,
                "agent_id": new_agent_id,
                "source_agent_id": source_agent_id,
                "agent_name": new_agent.name,
                "agent_description": new_agent.description,
                "message": f"Successfully cloned agent to '{new_agent.name}' with ID: {new_agent_id}",
            }

        except Exception as e:
            logger.error(f"Failed to clone agent: {e}")
            return {"success": False, "error": str(e), "agent_id": None}


class ListAgentsTool(BaseTool):
    """Tool for listing managed agents."""

    def __init__(self):
        super().__init__(
            name="list_agents",
            description="""List all dynamically created agents with their status and metadata.""",
        )

    async def call(
        self,
        tool_context: ToolContext,
        status: Optional[str] = None,
        creator: Optional[str] = None,
        include_templates: bool = False,
    ) -> Dict[str, Any]:
        """List agents.

        Args:
            tool_context: The tool execution context
            status: Filter by status (created, active, paused, stopped, error)
            creator: Filter by creator
            include_templates: Whether to include available templates

        Returns:
            Dictionary with agent list
        """
        try:
            manager = get_dynamic_agent_manager()
            registry = get_agent_registry()

            # Filter status
            status_filter = None
            if status:
                try:
                    status_filter = AgentStatus(status.lower())
                except ValueError:
                    return {
                        "success": False,
                        "error": f"Invalid status '{status}'. Valid values: created, active, paused, stopped, error",
                    }

            # Get agents
            agents = manager.list_agents(status=status_filter, creator=creator)

            agent_list = []
            for metadata in agents:
                agent_info = {
                    "id": metadata.id,
                    "name": metadata.name,
                    "type": metadata.type,
                    "status": metadata.status.value,
                    "created_at": metadata.created_at.isoformat(),
                    "updated_at": metadata.updated_at.isoformat(),
                    "creator": metadata.creator,
                    "description": metadata.description,
                    "parent_id": metadata.parent_id,
                    "children_count": len(metadata.children_ids),
                }
                agent_list.append(agent_info)

            result = {
                "success": True,
                "agents": agent_list,
                "total_count": len(agent_list),
            }

            # Include templates if requested
            if include_templates:
                result["available_templates"] = registry.list_templates()
                result["available_builders"] = registry.list_builders()

            return result

        except Exception as e:
            logger.error(f"Failed to list agents: {e}")
            return {"success": False, "error": str(e)}


class GetAgentInfoTool(BaseTool):
    """Tool for getting detailed agent information."""

    def __init__(self):
        super().__init__(
            name="get_agent_info",
            description="""Get detailed information about a specific agent.""",
        )

    async def call(self, tool_context: ToolContext, agent_id: str) -> Dict[str, Any]:
        """Get agent information.

        Args:
            tool_context: The tool execution context
            agent_id: ID of the agent to get info for

        Returns:
            Dictionary with agent information
        """
        try:
            manager = get_dynamic_agent_manager()

            agent = manager.get_agent(agent_id)
            metadata = manager.get_agent_metadata(agent_id)

            if not metadata:
                return {"success": False, "error": f"Agent {agent_id} not found"}

            agent_info = {
                "id": metadata.id,
                "name": metadata.name,
                "type": metadata.type,
                "status": metadata.status.value,
                "created_at": metadata.created_at.isoformat(),
                "updated_at": metadata.updated_at.isoformat(),
                "creator": metadata.creator,
                "description": metadata.description,
                "config": metadata.config,
                "parent_id": metadata.parent_id,
                "children_ids": metadata.children_ids,
                "is_active": agent is not None,
            }

            if agent:
                agent_info["current_tools"] = (
                    [tool.name for tool in agent.tools]
                    if hasattr(agent, 'tools')
                    else []
                )
                agent_info["sub_agents"] = (
                    [sub.name for sub in agent.sub_agents] if agent.sub_agents else []
                )

            return {"success": True, "agent": agent_info}

        except Exception as e:
            logger.error(f"Failed to get agent info: {e}")
            return {"success": False, "error": str(e)}


class RemoveAgentTool(BaseTool):
    """Tool for removing agents."""

    def __init__(self):
        super().__init__(
            name="remove_agent",
            description="""Remove a dynamically created agent from management.""",
        )

    async def call(
        self, tool_context: ToolContext, agent_id: str, cascade: bool = False
    ) -> Dict[str, Any]:
        """Remove an agent.

        Args:
            tool_context: The tool execution context
            agent_id: ID of the agent to remove
            cascade: Whether to also remove child agents

        Returns:
            Dictionary with removal result
        """
        try:
            manager = get_dynamic_agent_manager()

            success = await manager.remove_agent(agent_id, cascade=cascade)

            if success:
                return {
                    "success": True,
                    "message": f"Successfully removed agent {agent_id}",
                }
            else:
                return {"success": False, "error": f"Agent {agent_id} not found"}

        except Exception as e:
            logger.error(f"Failed to remove agent: {e}")
            return {"success": False, "error": str(e)}
