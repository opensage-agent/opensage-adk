from typing import Any, Dict, List, Optional, Union

from google.adk.agents.llm_agent import LlmAgent
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.models.base_llm import BaseLlm
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from aigise.session import AgentStatus, get_aigise_session
from aigise.toolbox.decorators import safe_tool_execution
from aigise.toolbox.general.agent_tools import complain
from aigise.toolbox.general.bash_tools_interface import (
    get_background_task_output,
    list_background_tasks,
    run_terminal_command,
)
from aigise.utils.agent_utils import (
    INHERIT_MODEL,
    extract_tools_from_agent,
    get_aigise_session_id_from_context,
    get_model_from_agent,
)


@safe_tool_execution
async def create_subagent(
    agent_name: str,
    instruction: str,
    model_name: str,
    tools_list: List[str],
    enabled_skills: Union[List[str], str],
    tool_context: ToolContext,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Dynamically create a sub-agent with specified tools and instructions.
    You should first list the existing sub-agents before creating a new one.

    IMPORTANT:
    - A subagent's capabilities come from two sources:
      1) **Python tools**: determined by `tools_list` (plus a small set of default
         baseline tools injected automatically, see below).
      2) **Bash tools**: determined by `enabled_skills` (which controls which
         `bash_tools/*` skills are loaded for the subagent).
    - `enabled_skills` can be empty. If it is empty/None, the subagent may not
      have any bash tools available. Choose it carefully based on what the
      subagent needs to do.
    - `tools_list` must NOT be empty. If it is empty, this tool will return an
      error and no subagent will be created.
    - Default baseline tools (always injected):
      `run_terminal_command`, `list_background_tasks`, `get_background_task_output`,
      `complain`.

    Args:
        agent_name: Custom name for the agent
        instruction: Custom instruction for the agent, this will be the system prompt for the agent, it should be a comprehensive instruction for the agent to follow and not task-specific.
        model_name: Model to use for the agent (e.g., "anthropic/claude-sonnet-4",
          "openai/gpt-5", or "inherit" to reuse the current agent's model)
        tools_list: List of tool names to assign to the agent
        enabled_skills: Controls which bash tools are loaded.
                      - None: Load NO bash tools.
                      - "all": Load ALL found bash tools.
                      - List[str]: Load specific tools by name (e.g. ["fuzz/simplified-python-fuzzer"]).
        description: Optional description for the agent

    Returns:
        Dictionary with creation result and agent details
    """
    try:
        session_id = get_aigise_session_id_from_context(tool_context)
        session = get_aigise_session(session_id)
        manager = session.agents
        ensemble_manager = session.ensemble
        available_models = ensemble_manager.get_available_models()
        if model_name not in available_models:
            return {
                "success": False,
                "error": f"Model '{model_name}' not available. Available models: {available_models}",
            }

        current_agent = tool_context._invocation_context.agent
        available_tools = extract_tools_from_agent(current_agent)

        if not available_tools:
            return {"success": False, "error": "No tools available from current agent"}

        if not tools_list:
            return {
                "success": False,
                "error": (
                    "tools_list must not be empty. Choose at least one Python tool "
                    "for the subagent. Note: baseline tools are always injected: "
                    "run_terminal_command, list_background_tasks, "
                    "get_background_task_output, complain."
                ),
            }

        default_tools_by_name = {
            "run_terminal_command": run_terminal_command,
            "list_background_tasks": list_background_tasks,
            "get_background_task_output": get_background_task_output,
            "complain": complain,
        }

        # Validate tools
        tools_to_add = []
        invalid_tools = []

        # Always inject default baseline tools.
        tools_to_add.extend(default_tools_by_name.values())

        # Validate non-default tool names against caller's available tools.
        for tool_name in tools_list:
            if tool_name in default_tools_by_name:
                continue
            if tool_name in available_tools:
                tools_to_add.append(available_tools[tool_name])
            else:
                invalid_tools.append(tool_name)

        if invalid_tools:
            return {
                "success": False,
                "error": f"Invalid tools: {invalid_tools}. Available tools: {list(available_tools.keys())}",
            }

        # Ensure tool_names include baseline tools (for metadata/debug visibility).
        tool_names_final = list(default_tools_by_name.keys())
        for name in tools_list:
            if name not in tool_names_final:
                tool_names_final.append(name)

        # Strengthen instruction: only restrict bash tools by enabled_skills.
        enabled_skills_repr = "None" if enabled_skills is None else repr(enabled_skills)
        skills_guardrail = (
            "\n\n[Tooling policy]\n"
            "Bash tools availability is controlled by enabled_skills. "
            f"For this subagent, enabled_skills={enabled_skills_repr}.\n"
            "You must only use bash tools that are available under the enabled_skills "
            "selection. If a needed bash tool is not available, report the limitation "
            "and ask the caller to recreate the subagent with the correct enabled_skills.\n"
        )

        config = {
            "name": agent_name,
            "instruction": instruction + skills_guardrail,
            "model": model_name,
            "description": description
            or f"Agent {agent_name} with tools: {', '.join(tools_list)}",
            "tool_names": tool_names_final,
            "tools": tools_to_add,
            "enabled_skills": enabled_skills,
        }
        if model_name == INHERIT_MODEL:
            config["_resolved_model"] = get_model_from_agent(current_agent)

        agent_id, agent_instance = await manager.create_agent(
            config, creator=current_agent.name
        )

        await manager.update_agent_status(agent_id, AgentStatus.ACTIVE)

        return {
            "success": True,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "model": model_name,
            "tools_assigned": tools_list,
            "enabled_skills": enabled_skills,
            # Return the effective instruction used by the created subagent.
            "instruction": config["instruction"],
            "description": config["description"],
            "message": f"Successfully created agent '{agent_name}' with model '{model_name}' and tools: {', '.join(tools_list)}",
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


def _extract_tool_names_from_metadata(metadata: Any) -> List[str]:
    """Extract tool names from agent metadata config"""
    return metadata.config.get("tool_names", []) if metadata.config else []


def _extract_tool_names_from_agent(agent_instance) -> List[str]:
    """Extract tool names from agent instance"""
    tool_names = []
    if agent_instance and agent_instance.tools:
        for tool in agent_instance.tools:
            tool_name = None
            if hasattr(tool, "name"):
                tool_name = tool.name
            elif hasattr(tool, "__name__"):
                tool_name = tool.__name__
            elif hasattr(tool, "func") and hasattr(tool.func, "__name__"):
                tool_name = tool.func.__name__
            tool_names.append(tool_name)
    return tool_names


@safe_tool_execution
async def list_active_agents(tool_context: ToolContext) -> Dict[str, Any]:
    """List all active sub-agents, loading persistent agents on demand.

    This function:
    1. Loads persisted agents on demand using caller's tools
    2. Returns information about all dynamically created agents (both in-memory and restored)
    """
    try:
        session_id = get_aigise_session_id_from_context(tool_context)
        session = get_aigise_session(session_id)
        manager = session.agents
        caller_agent = tool_context._invocation_context.agent

        # Extract tools from caller agent
        caller_tools = extract_tools_from_agent(caller_agent)

        # Load persisted agents on demand, rebuilding with caller tools if possible
        manager._load_persisted_agents_on_demand(caller_tools, caller_agent)

        # Get all dynamic agents (both in-memory and restored) for current session
        all_agents = manager.list_agents()
        active_agents = []

        # Process dynamic agents
        for agent_metadata in all_agents:
            # Try to get agent instance from current session
            agent_instance = manager.get_agent(agent_metadata.id)

            # Determine tool names and enabled_skills
            if agent_instance:
                tool_names = _extract_tool_names_from_agent(agent_instance)
                # Get enabled_skills from agent_instance
                enabled_skills = getattr(agent_instance, "_enabled_skills", None)
            else:
                # Agent not loaded, get tool names and enabled_skills from metadata
                tool_names = _extract_tool_names_from_metadata(agent_metadata)
                enabled_skills = (
                    agent_metadata.config.get("enabled_skills")
                    if agent_metadata.config
                    else None
                )
            if agent_instance is not None:
                active_agents.append(
                    {
                        "name": agent_metadata.name,
                        "description": agent_metadata.description,
                        "tools": tool_names,
                        "model": agent_metadata.config.get(
                            "model", "anthropic/claude-sonnet-4-20250514"
                        )
                        if agent_metadata.config
                        else "anthropic/claude-sonnet-4-20250514",
                        "enabled_skills": enabled_skills,
                        "type": "dynamic_agent",
                    }
                )

        return {
            "success": True,
            "active_agents": active_agents,
            "dynamic_agents_count": len(
                [a for a in active_agents if a.get("type") == "dynamic_agent"]
            ),
            "adk_subagents_count": len(
                [a for a in active_agents if a.get("type") == "adk_subagent"]
            ),
            "total_count": len(active_agents),
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


@safe_tool_execution
async def call_subagent_as_tool(
    agent_name: str, task_message: str, tool_context: ToolContext
) -> Dict[str, Any]:
    """
    Call a sub-agent as a tool - Agent as a Tool pattern.
    You should first list the existing sub-agents before trying to call one.

    This supports both dynamic agents and the current agent's subagents (only LlmAgent types).

    This treats the sub-agent as a specialized tool that can process
    natural language requests and return structured results.

    Args:
        agent_name: Name of the sub-agent to call
        task_message: Natural language task description

    Returns:
        Result from the sub-agent execution
    """
    try:
        session_id = get_aigise_session_id_from_context(tool_context)
        session = get_aigise_session(session_id)
        manager = session.agents
        caller_agent = tool_context._invocation_context.agent

        # First try to find in dynamic agents within current session
        all_agents = manager.list_agents()
        target_agent_metadata = None
        agent_instance = None

        for agent_metadata in all_agents:
            if agent_metadata.name == agent_name:
                target_agent_metadata = agent_metadata
                agent_instance = manager.get_agent(agent_metadata.id)
                if agent_instance:
                    break

        # If not found in dynamic agents, try ADK subagents (only LlmAgent types)
        if (
            not agent_instance
            and hasattr(caller_agent, "sub_agents")
            and caller_agent.sub_agents
        ):
            for sub_agent in caller_agent.sub_agents:
                if sub_agent.name == agent_name and isinstance(sub_agent, LlmAgent):
                    agent_instance = sub_agent
                    break

        if not agent_instance:
            return {
                "success": False,
                "error": f"Sub-agent '{agent_name}' not found. Create one first.",
            }

        # Create AgentTool and call it using standard ADK way
        agent_tool = AgentTool(agent=agent_instance)

        # Prepare args for AgentTool (following ADK standard)
        tool_args = {"request": task_message}

        # Call AgentTool using standard run_async method
        tool_result = await agent_tool.run_async(
            args=tool_args, tool_context=tool_context
        )

        # Determine agent type and ID
        agent_id = target_agent_metadata.id if target_agent_metadata else "adk_subagent"
        agent_type = "dynamic_agent" if target_agent_metadata else "adk_subagent"

        return {
            "success": True,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "agent_type": agent_type,
            "task_message": task_message,
            "response": str(tool_result),
            "message": f"Sub-agent '{agent_name}' ({agent_type}) executed as tool successfully",
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to call sub-agent as tool: {str(e)}",
        }
