from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Union

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools.agent_tool import AgentTool

try:
    from .dynamic_agent_manager import get_dynamic_agent_manager
except ImportError:
    # Handle case where dynamic_agent_manager is not available
    get_dynamic_agent_manager = None

logger = logging.getLogger("aigise.extended_features." + __name__)


@dataclass
class EnsembleAgentInfo:
    """Information about an agent available for ensemble."""

    name: str
    description: str
    tools: List[str]
    model: str
    agent_type: str  # "dynamic_agent", "adk_subagent", "agent_tool"
    agent_instance: Optional[BaseAgent] = None
    source_path: Optional[str] = None  # For tracking where the agent was found


class AgentEnsembleManager:
    """Manager for agent ensemble functionality with thread-safe tools management."""

    def __init__(self):
        self._thread_safe_tools: Set[str] = set()
        self._load_thread_safe_tools_from_env()

    def _load_thread_safe_tools_from_env(self) -> None:
        """Load thread-safe tools from environment variable."""
        thread_safe_tools_str = os.getenv("THREAD_SAFE_TOOLS", "")
        if thread_safe_tools_str:
            self._thread_safe_tools = set(
                tool.strip()
                for tool in thread_safe_tools_str.split(",")
                if tool.strip()
            )
            logger.info(
                f"Loaded {len(self._thread_safe_tools)} thread-safe tools from environment"
            )

    def add_thread_safe_tool(self, tool_name: str) -> None:
        """Add a tool to the thread-safe tools list."""
        self._thread_safe_tools.add(tool_name)
        self._update_env_variable()
        logger.info(f"Added thread-safe tool: {tool_name}")

    def add_thread_safe_tools(self, tool_names: List[str]) -> None:
        """Add multiple tools to the thread-safe tools list."""
        self._thread_safe_tools.update(tool_names)
        self._update_env_variable()
        logger.info(f"Added {len(tool_names)} thread-safe tools")

    def remove_thread_safe_tool(self, tool_name: str) -> bool:
        """Remove a tool from the thread-safe tools list. Returns True if removed."""
        if tool_name in self._thread_safe_tools:
            self._thread_safe_tools.remove(tool_name)
            self._update_env_variable()
            logger.info(f"Removed thread-safe tool: {tool_name}")
            return True
        return False

    def get_thread_safe_tools(self) -> Set[str]:
        """Get the current set of thread-safe tools."""
        return self._thread_safe_tools.copy()

    def is_tool_thread_safe(self, tool_name: str) -> bool:
        """Check if a tool is thread-safe."""
        return tool_name in self._thread_safe_tools

    def _update_env_variable(self) -> None:
        """Update the THREAD_SAFE_TOOLS environment variable."""
        os.environ["THREAD_SAFE_TOOLS"] = ",".join(sorted(self._thread_safe_tools))

    def _extract_tool_names_from_agent(self, agent: BaseAgent) -> List[str]:
        """Extract tool names from an agent instance."""
        tool_names = []
        if agent and hasattr(agent, "tools") and agent.tools:
            for tool in agent.tools:
                tool_name = None
                if hasattr(tool, "name"):
                    tool_name = tool.name
                elif hasattr(tool, "__name__"):
                    tool_name = tool.__name__
                elif hasattr(tool, "func") and hasattr(tool.func, "__name__"):
                    tool_name = tool.func.__name__
                if tool_name:
                    tool_names.append(tool_name)
        return tool_names

    def _discover_subagents_recursive(
        self,
        agent: BaseAgent,
        discovered: List[EnsembleAgentInfo],
        visited: Set[str],
        path: str = "",
    ) -> None:
        """Recursively discover all LlmAgent subagents from an agent."""
        if not hasattr(agent, "sub_agents") or not agent.sub_agents:
            return

        for i, sub_agent in enumerate(agent.sub_agents):
            # Avoid infinite recursion
            if sub_agent.name in visited:
                continue
            visited.add(sub_agent.name)

            current_path = f"{path}.sub_agents[{i}]" if path else f"sub_agents[{i}]"

            # Only include LlmAgent subagents
            if isinstance(sub_agent, LlmAgent):
                tools = self._extract_tool_names_from_agent(sub_agent)
                model = (
                    str(sub_agent.model)
                    if hasattr(sub_agent, "model") and sub_agent.model
                    else "default"
                )

                discovered.append(
                    EnsembleAgentInfo(
                        name=sub_agent.name,
                        description=sub_agent.description
                        or f"ADK LlmAgent subagent: {sub_agent.name}",
                        tools=tools,
                        model=model,
                        agent_type="adk_subagent",
                        agent_instance=sub_agent,
                        source_path=current_path,
                    )
                )

                # Recursively check this subagent's subagents
                self._discover_subagents_recursive(
                    sub_agent, discovered, visited, current_path
                )

    def _discover_agent_tools_recursive(
        self,
        agent: BaseAgent,
        discovered: List[EnsembleAgentInfo],
        visited: Set[str],
        path: str = "",
    ) -> None:
        """Recursively discover all AgentTool instances from an agent's tools."""
        if not hasattr(agent, "tools") or not agent.tools:
            return

        for i, tool in enumerate(agent.tools):
            if isinstance(tool, AgentTool):
                tool_agent = tool.agent
                if tool_agent.name in visited:
                    continue
                visited.add(tool_agent.name)

                current_path = (
                    f"{path}.tools[{i}].agent" if path else f"tools[{i}].agent"
                )

                # Only include LlmAgent instances in AgentTools
                if isinstance(tool_agent, LlmAgent):
                    tools = self._extract_tool_names_from_agent(tool_agent)
                    model = (
                        str(tool_agent.model)
                        if hasattr(tool_agent, "model") and tool_agent.model
                        else "default"
                    )

                    discovered.append(
                        EnsembleAgentInfo(
                            name=tool_agent.name,
                            description=tool_agent.description
                            or f"AgentTool LlmAgent: {tool_agent.name}",
                            tools=tools,
                            model=model,
                            agent_type="agent_tool",
                            agent_instance=tool_agent,
                            source_path=current_path,
                        )
                    )

                    # Recursively check this agent's subagents and agent tools
                    self._discover_subagents_recursive(
                        tool_agent, discovered, visited, current_path
                    )
                    self._discover_agent_tools_recursive(
                        tool_agent, discovered, visited, current_path
                    )

    def discover_all_static_agents(
        self, root_agent: BaseAgent
    ) -> List[EnsembleAgentInfo]:
        """Discover all static subagents and agent tools recursively from a root agent."""
        discovered = []
        visited = set()

        # Discover direct subagents
        self._discover_subagents_recursive(root_agent, discovered, visited, "root")

        # Discover agent tools
        self._discover_agent_tools_recursive(root_agent, discovered, visited, "root")

        logger.info(
            f"Discovered {len(discovered)} static agents from root agent '{root_agent.name}'"
        )
        return discovered

    def filter_thread_safe_agents(
        self, agents: List[EnsembleAgentInfo]
    ) -> Dict[str, List[EnsembleAgentInfo]]:
        """Filter agents based on thread-safe tools coverage."""
        safe_agents = []
        unsafe_agents = []

        for agent in agents:
            agent_tools = set(agent.tools)

            # Check if all agent tools are in THREAD_SAFE_TOOLS
            if agent_tools.issubset(self._thread_safe_tools):
                safe_agents.append(agent)
            else:
                # Find tools that are not thread-safe
                unsafe_tools = agent_tools - self._thread_safe_tools
                # Add unsafe_tools info to the agent for debugging
                agent_info = EnsembleAgentInfo(
                    name=agent.name,
                    description=agent.description,
                    tools=agent.tools,
                    model=agent.model,
                    agent_type=agent.agent_type,
                    agent_instance=agent.agent_instance,
                    source_path=agent.source_path,
                )
                # Store unsafe tools in a custom attribute
                setattr(agent_info, "unsafe_tools", list(unsafe_tools))
                unsafe_agents.append(agent_info)

        logger.info(
            f"Filtered agents: {len(safe_agents)} safe, {len(unsafe_agents)} unsafe"
        )
        return {"safe_agents": safe_agents, "unsafe_agents": unsafe_agents}

    def get_ensemble_ready_agents(
        self, root_agent: BaseAgent, include_dynamic: bool = True
    ) -> Dict[str, Any]:
        """Get all agents ready for ensemble from both static and dynamic sources."""
        result = {
            "static_agents": [],
            "dynamic_agents": [],
            "safe_agents": [],
            "unsafe_agents": [],
            "thread_safe_tools": list(self._thread_safe_tools),
            "summary": {},
        }

        # Discover static agents
        static_agents = self.discover_all_static_agents(root_agent)
        filtered_static = self.filter_thread_safe_agents(static_agents)

        result["static_agents"] = static_agents
        result["safe_agents"].extend(filtered_static["safe_agents"])
        result["unsafe_agents"].extend(filtered_static["unsafe_agents"])

        # Include dynamic agents if requested
        if include_dynamic and get_dynamic_agent_manager is not None:
            try:
                manager = get_dynamic_agent_manager()
                all_dynamic = manager.list_agents()

                dynamic_agents = []
                for agent_metadata in all_dynamic:
                    agent_instance = manager.get_agent(agent_metadata.id)
                    if agent_instance:
                        tools = self._extract_tool_names_from_agent(agent_instance)
                        model = (
                            agent_metadata.config.get("model", "default")
                            if agent_metadata.config
                            else "default"
                        )

                        dynamic_agents.append(
                            EnsembleAgentInfo(
                                name=agent_metadata.name,
                                description=agent_metadata.description,
                                tools=tools,
                                model=model,
                                agent_type="dynamic_agent",
                                agent_instance=agent_instance,
                                source_path=f"dynamic_agent:{agent_metadata.id}",
                            )
                        )

                filtered_dynamic = self.filter_thread_safe_agents(dynamic_agents)
                result["dynamic_agents"] = dynamic_agents
                result["safe_agents"].extend(filtered_dynamic["safe_agents"])
                result["unsafe_agents"].extend(filtered_dynamic["unsafe_agents"])

            except Exception as e:
                logger.warning(f"Failed to include dynamic agents: {e}")

        # Generate summary
        result["summary"] = {
            "total_static_agents": len(static_agents),
            "total_dynamic_agents": len(result["dynamic_agents"]),
            "total_safe_agents": len(result["safe_agents"]),
            "total_unsafe_agents": len(result["unsafe_agents"]),
            "thread_safe_tools_count": len(self._thread_safe_tools),
        }

        return result


# Global manager instance
_global_ensemble_manager: Optional[AgentEnsembleManager] = None


def get_agent_ensemble_manager() -> AgentEnsembleManager:
    """Get the global agent ensemble manager instance."""
    global _global_ensemble_manager
    if _global_ensemble_manager is None:
        _global_ensemble_manager = AgentEnsembleManager()
    return _global_ensemble_manager
