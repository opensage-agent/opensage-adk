"""
OpenSageEnsembleManager: Session-specific agent ensemble management

This module provides session-bound agent ensemble management, replacing the global
AgentEnsembleManager with session-isolated ensemble handling.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Union

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.session.message_board import message_board_context
from opensage.session.opensage_dynamic_agent_manager import DynamicAgentManager
from opensage.utils.agent_utils import (
    INHERIT_MODEL,
    _copy_agent_with_updated_model_v2,
    extract_tools_from_agent,
    get_model_from_agent,
)

logger = logging.getLogger(__name__)


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
    enabled_skills: Optional[Union[List[str], str]] = (
        None  # enabled_skills from OpenSageAgent
    )


class OpenSageEnsembleManager:
    """Session-specific manager for agent ensemble functionality.

    Each OpenSageSession gets its own OpenSageEnsembleManager instance,
    ensuring complete ensemble configuration isolation between sessions.
    """

    def __init__(self, session):
        """Initialize OpenSageEnsembleManager.

        Args:
            session: OpenSageSession instance (stores reference, not copied)"""
        self._session = session
        self.opensage_session_id = session.opensage_session_id

        logger.info(
            f"Created OpenSageEnsembleManager for session: {session.opensage_session_id}"
        )

    @property
    def config(self):
        """Get latest config from session dynamically."""
        return self._session.config

    @property
    def agent_manager(self):
        """Get agent manager from session dynamically."""
        return self._session.agents

    def get_thread_safe_tools(self) -> Set[str]:
        """Get the current set of thread-safe tools from configuration."""
        config = self.config
        if config.agent_ensemble and config.agent_ensemble.thread_safe_tools:
            return config.agent_ensemble.thread_safe_tools.copy()
        return set()

    def add_thread_safe_tool(self, tool_name: str) -> None:
        """Add a tool to the thread-safe tools list."""
        config = self.config
        if not config.agent_ensemble:
            from ..config.config_dataclass import AgentEnsembleConfig

            config.agent_ensemble = AgentEnsembleConfig()

        config.agent_ensemble.thread_safe_tools.add(tool_name)
        logger.info(
            f"Added thread-safe tool: {tool_name} to session {self.opensage_session_id}"
        )

    def add_thread_safe_tools(self, tool_names: List[str]) -> None:
        """Add multiple tools to the thread-safe tools list."""
        config = self.config
        if not config.agent_ensemble:
            from ..config.config_dataclass import AgentEnsembleConfig

            config.agent_ensemble = AgentEnsembleConfig()

        config.agent_ensemble.thread_safe_tools.update(tool_names)
        logger.info(
            f"Added {len(tool_names)} thread-safe tools to session {self.opensage_session_id}"
        )

    def remove_thread_safe_tool(self, tool_name: str) -> bool:
        """Remove a tool from the thread-safe tools list. Returns True if removed."""
        config = self.config
        if not config.agent_ensemble or not config.agent_ensemble.thread_safe_tools:
            return False

        if tool_name in config.agent_ensemble.thread_safe_tools:
            config.agent_ensemble.thread_safe_tools.remove(tool_name)
            logger.info(
                f"Removed thread-safe tool: {tool_name} from session {self.opensage_session_id}"
            )
            return True
        return False

    def is_tool_thread_safe(self, tool_name: str) -> bool:
        """Check if a tool is thread-safe."""
        return tool_name in self.get_thread_safe_tools()

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

            # Only include OpenSageAgent subagents (ensemble only supports OpenSageAgent)
            if isinstance(sub_agent, OpenSageAgent):
                tools = self._extract_tool_names_from_agent(sub_agent)
                model = (
                    str(sub_agent.model)
                    if hasattr(sub_agent, "model") and sub_agent.model
                    else "default"
                )
                # Extract enabled_skills from OpenSageAgent
                enabled_skills = getattr(sub_agent, "_enabled_skills", None)

                discovered.append(
                    EnsembleAgentInfo(
                        name=sub_agent.name,
                        description=sub_agent.description
                        or f"OpenSageAgent subagent: {sub_agent.name}",
                        tools=tools,
                        model=model,
                        agent_type="adk_subagent",
                        agent_instance=sub_agent,
                        source_path=current_path,
                        enabled_skills=enabled_skills,
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

                # Only include OpenSageAgent instances in AgentTools (ensemble only supports OpenSageAgent)
                if isinstance(tool_agent, OpenSageAgent):
                    tools = self._extract_tool_names_from_agent(tool_agent)
                    model = (
                        str(tool_agent.model)
                        if hasattr(tool_agent, "model") and tool_agent.model
                        else "default"
                    )
                    # Extract enabled_skills from OpenSageAgent
                    enabled_skills = getattr(tool_agent, "_enabled_skills", None)

                    discovered.append(
                        EnsembleAgentInfo(
                            name=tool_agent.name,
                            description=tool_agent.description
                            or f"AgentTool OpenSageAgent: {tool_agent.name}",
                            tools=tools,
                            model=model,
                            agent_type="agent_tool",
                            agent_instance=tool_agent,
                            source_path=current_path,
                            enabled_skills=enabled_skills,
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
            f"Discovered {len(discovered)} static agents from root agent '{root_agent.name}' "
            f"in session {self.opensage_session_id}"
        )
        return discovered

    def filter_thread_safe_agents(
        self, agents: List[EnsembleAgentInfo]
    ) -> Dict[str, List[EnsembleAgentInfo]]:
        """Filter agents based on thread-safe tools coverage."""
        config = self.config
        enforce = True
        if config and config.agent_ensemble:
            enforce = getattr(config.agent_ensemble, "enforce_thread_safe_tools", True)

        if not enforce:
            return {"safe_agents": agents, "unsafe_agents": []}

        safe_agents = []
        unsafe_agents = []
        thread_safe_tools = self.get_thread_safe_tools()

        for agent in agents:
            agent_tools = set(agent.tools)

            # Check if all agent tools are in thread_safe_tools
            if agent_tools.issubset(thread_safe_tools):
                safe_agents.append(agent)
            else:
                # Find tools that are not thread-safe
                unsafe_tools = agent_tools - thread_safe_tools
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
        return {"safe_agents": safe_agents, "unsafe_agents": unsafe_agents}

    def get_ensemble_ready_agents(
        self, current_agent: BaseAgent, include_dynamic: bool = True
    ) -> Dict[str, Any]:
        """Get all agents ready for ensemble from both static and dynamic sources."""
        thread_safe_tools = self.get_thread_safe_tools()

        result = {
            "static_agents": [],
            "dynamic_agents": [],
            "safe_agents": [],
            "unsafe_agents": [],
            "thread_safe_tools": list(thread_safe_tools),
            "summary": {},
        }

        # Discover static agents
        static_agents = self.discover_all_static_agents(current_agent)
        filtered_static = self.filter_thread_safe_agents(static_agents)
        logger.info(
            f"Filtered static agents in session {self.opensage_session_id}: {len(filtered_static['safe_agents'])} safe, {len(filtered_static['unsafe_agents'])} unsafe"
        )

        result["static_agents"] = static_agents
        result["safe_agents"].extend(filtered_static["safe_agents"])
        result["unsafe_agents"].extend(filtered_static["unsafe_agents"])

        # Include dynamic agents if requested
        if include_dynamic:
            try:
                caller_tools = extract_tools_from_agent(current_agent)
                self.agent_manager._load_persisted_agents_on_demand(
                    caller_tools, current_agent
                )

                # Get dynamic agents from this session's agent manager (includes loaded ones)
                all_dynamic = self.agent_manager.list_agents()

                dynamic_agents = []
                for agent_metadata in all_dynamic:
                    agent_instance = self.agent_manager.get_agent(agent_metadata.id)
                    # Only include OpenSageAgent instances (ensemble only supports OpenSageAgent)
                    if agent_instance and isinstance(agent_instance, OpenSageAgent):
                        tools = self._extract_tool_names_from_agent(agent_instance)
                        model = (
                            agent_metadata.config.get("model", "default")
                            if agent_metadata.config
                            else "default"
                        )
                        # Extract enabled_skills from OpenSageAgent
                        enabled_skills = getattr(
                            agent_instance, "_enabled_skills", None
                        )

                        dynamic_agents.append(
                            EnsembleAgentInfo(
                                name=agent_metadata.name,
                                description=agent_metadata.description
                                or f"Dynamic agent: {agent_metadata.name}",
                                tools=tools,
                                model=model,
                                agent_type="dynamic_agent",
                                agent_instance=agent_instance,
                                source_path=f"dynamic_agent:{agent_metadata.id}",
                                enabled_skills=enabled_skills,
                            )
                        )

                filtered_dynamic = self.filter_thread_safe_agents(dynamic_agents)
                logger.info(
                    f"Filtered dynamic agents in session {self.opensage_session_id}: {len(filtered_dynamic['safe_agents'])} safe, {len(filtered_dynamic['unsafe_agents'])} unsafe"
                )
                result["dynamic_agents"] = dynamic_agents
                result["safe_agents"].extend(filtered_dynamic["safe_agents"])
                result["unsafe_agents"].extend(filtered_dynamic["unsafe_agents"])

            except Exception as e:
                logger.warning(
                    f"Failed to include dynamic agents in session {self.opensage_session_id}: {e}"
                )

        # Generate summary
        result["summary"] = {
            "opensage_session_id": self.opensage_session_id,
            "total_static_agents": len(static_agents),
            "total_dynamic_agents": len(result["dynamic_agents"]),
            "total_safe_agents": len(result["safe_agents"]),
            "total_unsafe_agents": len(result["unsafe_agents"]),
            "thread_safe_tools_count": len(thread_safe_tools),
        }

        return result

    def get_available_models(self) -> List[str]:
        """Get available models for ensemble from configuration."""
        config = self.config
        if (
            config.agent_ensemble
            and config.agent_ensemble.available_models_for_ensemble
        ):
            return config.agent_ensemble.available_models_for_ensemble.copy()
        return []

    async def execute_agent_ensemble(
        self,
        full_instruction: str,
        target_agent_info: EnsembleAgentInfo,
        model_name_to_count: dict[str, int],
        current_agent: BaseAgent,
        tool_context: ToolContext,
    ) -> dict:
        """Execute agent ensemble with multiple models and aggregate results.

                Args:
                    full_instruction (str): Complete instruction including optional history
                    target_agent_info (EnsembleAgentInfo): Validated EnsembleAgentInfo for the target agent
                    model_name_to_count (dict[str, int]): Dictionary of model names and count of agents per model
                    current_agent (BaseAgent): Current agent from tool context

        Raises:
          ValueError: Raised when this operation fails.
                Returns:
                    dict: Dictionary with success status and aggregated response or error details
        """
        from opensage.agents.opensage_agent import OpenSageAgent

        # Validate that target agent is OpenSageAgent
        if not target_agent_info.agent_instance or not isinstance(
            target_agent_info.agent_instance, OpenSageAgent
        ):
            return {
                "success": False,
                "error": f"Agent '{target_agent_info.name}' must be an OpenSageAgent instance for ensemble. Got: {type(target_agent_info.agent_instance)}",
            }

        board_id = f"ensemble_{uuid.uuid4().hex[:12]}"
        # Ensure board exists before any agent tries to post.
        self._session.get_message_board(board_id=board_id)
        try:
            # Create multiple agent execution tasks
            agent_tasks = []
            task_descriptions = []

            for model_name, count in model_name_to_count.items():
                for i in range(count):
                    task_id = f"{model_name}_{i + 1}"
                    task_descriptions.append(
                        f"Agent {task_id} using model {model_name}"
                    )

                    # Create individual task with model-specific instruction
                    peers_lines = (
                        "\n".join(task_descriptions) if task_descriptions else ""
                    )
                    enhanced_task_message = f"""
You are running as part of an agent ensemble using model: {model_name}
Task ID: {task_id}

Peers running in parallel:
{peers_lines}

Coordination:
- Use the shared message board to coordinate with peers.
- Call `post_to_board(message=...)` to share findings/plan updates.
- Tool results may include `_message_board_diff`; you MUST read it and incorporate any new info.

{full_instruction}

Please provide your unique perspective and analysis. Consider that other agents using different models will also analyze this, so focus on your strengths and provide diverse insights.
"""

                    # Create new agent instance with the specified model
                    # enabled_skills will be automatically extracted from target_agent_info
                    try:
                        instance_id = f"{target_agent_info.name}__{task_id}"
                        if model_name == INHERIT_MODEL:
                            inherit_model = get_model_from_agent(current_agent)
                            if inherit_model is None:
                                raise ValueError(
                                    "model_name='inherit' but current agent has no model"
                                )
                            agent_with_model = _copy_agent_with_updated_model_v2(
                                target_agent_info,
                                model_name,
                                inherit_model=inherit_model,
                            )
                        else:
                            agent_with_model = _copy_agent_with_updated_model_v2(
                                target_agent_info, model_name
                            )

                        # Stable per-instance identity for message board cursors.
                        setattr(agent_with_model, "_instance_id", instance_id)

                        # Ensure message board posting tool exists for all ensemble instances.
                        from opensage.toolbox.general.message_board_tools import (
                            post_to_board,
                        )
                        from opensage.toolbox.tool_normalization import (
                            make_tool_safe_dict,
                        )

                        post_tool = make_tool_safe_dict(post_to_board)
                        if not isinstance(
                            getattr(agent_with_model, "tools", None), list
                        ):
                            agent_with_model.tools = list(agent_with_model.tools or [])
                        existing_names = {
                            getattr(t, "name", None)
                            for t in (agent_with_model.tools or [])
                        }
                        if post_tool.name not in existing_names:
                            agent_with_model.tools.append(post_tool)

                        # Wrap the agent in AgentTool and call it directly
                        agent_tool = AgentTool(agent=agent_with_model)

                        # Fix closure issue by capturing current values as default parameters
                        async def agent_tool_call(
                            captured_agent_tool=agent_tool,
                            captured_task_message=enhanced_task_message,
                            captured_agent_name=agent_with_model.name,
                            captured_model_name=model_name,
                        ):
                            try:
                                # Call the AgentTool directly
                                result = await captured_agent_tool.run_async(
                                    args={"request": captured_task_message},
                                    tool_context=tool_context,
                                )

                                return {
                                    "success": True,
                                    "response": str(result),
                                    "agent_name": captured_agent_name,
                                    "model": captured_model_name,
                                }
                            except Exception as e:
                                return {
                                    "success": False,
                                    "error": f"AgentTool call failed: {str(e)}",
                                    "agent_name": captured_agent_name,
                                    "model": captured_model_name,
                                }

                        agent_task = agent_tool_call()

                    except Exception as e:
                        # Fallback to original method if agent creation fails
                        print(
                            f"Warning: Failed to create agent with model {model_name}, using original agent: {e}"
                        )
                        from opensage.toolbox.general.dynamic_subagent import (
                            call_subagent_as_tool,
                        )

                        agent_task = call_subagent_as_tool(
                            agent_name=target_agent_info.name,
                            task_message=enhanced_task_message,
                            tool_context=tool_context,
                        )

                    agent_tasks.append((task_id, model_name, agent_task))

            # Step 4: Execute all agents in parallel
            print(f"Launching {len(agent_tasks)} agent instances...")

            # Create all tasks (start parallel execution immediately)
            tasks = []
            for task_id, model_name, task_coroutine in agent_tasks:
                # Use default parameters to capture current loop values (avoid closure issue)
                async def execute_agent_task(
                    coro, tid=task_id, mn=model_name, bid=board_id
                ):
                    try:
                        with message_board_context(bid):
                            result = await coro
                        return {
                            "task_id": tid,
                            "model_name": mn,
                            "success": result.get("success", False),
                            "response": result.get("response", ""),
                            "error": result.get("error", None),
                        }
                    except Exception as e:
                        return {
                            "task_id": tid,
                            "model_name": mn,
                            "success": False,
                            "response": "",
                            "error": str(e),
                        }

                task = asyncio.create_task(execute_agent_task(task_coroutine))
                tasks.append(task)

            # Collect all results (tasks are already running in parallel)
            task_results = []
            for task in tasks:
                result = await task
                task_results.append(result)

            # Collect successful results
            successful_results = [r for r in task_results if r["success"]]
            failed_results = [r for r in task_results if not r["success"]]

            if not successful_results:
                return {
                    "success": False,
                    "error": "All agent executions failed",
                    "failed_results": failed_results,
                    "total_attempted": len(agent_tasks),
                }

            # Step 5: Aggregate results using LLM
            aggregation_prompt = f"""
You are aggregating responses from {len(successful_results)} different AI agents that analyzed the same instruction using different models.

Original instruction given to all agents:
{full_instruction}

Agent Responses:
"""

            for i, result in enumerate(successful_results):
                aggregation_prompt += f"""

=== Agent {result["task_id"]} (Model: {result["model_name"]}) ===
{result["response"]}
"""

            aggregation_prompt += f"""

=== AGGREGATION INSTRUCTIONS ===
Please analyze all the above responses and create a comprehensive, well-reasoned final answer that:

1. Synthesizes the best insights from all agents
2. Identifies areas of consensus and disagreement
3. Provides a balanced, nuanced perspective
4. Highlights unique insights that only emerged from the ensemble approach
5. Gives a clear, actionable conclusion

If there are significant disagreements between agents, explain the different perspectives and provide your reasoned judgment on which approach is most sound.

Final aggregated response:
"""

            # Aggregate results
            model_name = self.config.llm.summarize_model
            if not model_name or model_name == INHERIT_MODEL:
                if not model_name:
                    logger.warning(
                        "summarize model not configured in LLM settings, trying to use agent model"
                    )
                model = get_model_from_agent(current_agent)
                if model is None:
                    raise ValueError("Unable to resolve current agent model")
            else:
                model = LiteLlm(model=model_name)

            llm_request = LlmRequest()
            llm_request.config = types.GenerateContentConfig()
            llm_request.contents = [
                types.Content(
                    role="user", parts=[types.Part.from_text(text=aggregation_prompt)]
                )
            ]

            aggregated_response = ""
            async for llm_response in model.generate_content_async(llm_request):
                if llm_response.content and llm_response.content.parts:
                    for part in llm_response.content.parts:
                        if part.text:
                            aggregated_response += part.text

            return {
                "success": True,
                "aggregated_response": aggregated_response.strip(),
                "message": f"Successfully ran ensemble with {len(successful_results)}/{len(agent_tasks)} agents",
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Agent ensemble failed: {str(e)}",
                "agent_name": target_agent_info.name,
            }
        finally:
            self._session.cleanup_message_board(board_id=board_id)

    def cleanup(self) -> None:
        """Cleanup ensemble manager resources for this session."""
        logger.info(
            f"Cleaning up OpenSageEnsembleManager for session {self.opensage_session_id}"
        )
        # No specific cleanup needed for ensemble manager
