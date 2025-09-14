from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.llm_agent import LlmAgent, _SingleAfterToolCallback
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.tool_context import ToolContext


async def discover_all_agents_async(
    root_agent: BaseAgent, context: Optional[ReadonlyContext] = None
) -> List[BaseAgent]:
    """Recursively discover all agents in the agent tree.

    Args:
        root_agent: The root agent to start discovery from
        context: Optional context for tool resolution

    Returns:
        List of all discovered agents including root, sub-agents, and tool agents
    """
    discovered_names: Set[str] = set()
    agents: List[BaseAgent] = []
    await _discover_agents_recursive(root_agent, agents, discovered_names, context)
    return agents


async def _discover_agents_recursive(
    agent: BaseAgent,
    agents: List[BaseAgent],
    discovered_names: Set[str],
    context: Optional[ReadonlyContext] = None,
) -> None:
    """Recursively discover agents."""
    # Avoid infinite loops by tracking agent names
    if agent.name in discovered_names:
        return

    discovered_names.add(agent.name)
    agents.append(agent)

    # 1. Discover sub-agents
    for sub_agent in agent.sub_agents:
        await _discover_agents_recursive(sub_agent, agents, discovered_names, context)

    # 2. Discover agents in tools (only for LlmAgent)
    if isinstance(agent, LlmAgent):
        try:
            tools = await agent.canonical_tools(context)
            await _discover_agents_from_tools(tools, agents, discovered_names, context)
        except Exception as e:
            print(f"Warning: Failed to get tools for agent {agent.name}: {e}")


async def _discover_agents_from_tools(
    tools: List[BaseTool],
    agents: List[BaseAgent],
    discovered_names: Set[str],
    context: Optional[ReadonlyContext] = None,
) -> None:
    """Discover agents from tools."""
    for tool in tools:
        # Check if tool is an AgentTool
        if isinstance(tool, AgentTool):
            await _discover_agents_recursive(
                tool.agent, agents, discovered_names, context
            )

        # Check if tool has nested agents (for complex tools)
        if hasattr(tool, "agent") and isinstance(tool.agent, BaseAgent):
            await _discover_agents_recursive(
                tool.agent, agents, discovered_names, context
            )

        # Handle toolsets that might contain agent tools
        if isinstance(tool, BaseToolset):
            try:
                nested_tools = await tool.get_tools(context)
                await _discover_agents_from_tools(
                    nested_tools, agents, discovered_names, context
                )
            except Exception as e:
                print(f"Warning: Failed to get tools from toolset {tool}: {e}")


def register_callback_to_all_agents(
    agents: List[BaseAgent], callbacks: List[_SingleAfterToolCallback]
) -> Dict[str, bool]:
    """Register multiple after_tool_callbacks to all agents.

    Args:
        agents: List of agents to register callbacks to
        callbacks: List of callback functions to register

    Returns:
        Dict mapping agent names to registration success status
    """
    results = {}

    for agent in agents:
        if isinstance(agent, LlmAgent):
            try:
                success = _add_callbacks_to_agent(agent, callbacks)
                results[agent.name] = success
            except Exception as e:
                print(f"Failed to register callbacks to agent {agent.name}: {e}")
                results[agent.name] = False
        else:
            # Non-LlmAgent types don't support after_tool_callback
            results[agent.name] = False

    return results


def register_callback_to_all_agents_single(
    agents: List[BaseAgent], callback: _SingleAfterToolCallback
) -> Dict[str, bool]:
    """Register a single after_tool_callback to all agents.

    Args:
        agents: List of agents to register callback to
        callback: The callback function to register

    Returns:
        Dict mapping agent names to registration success status
    """
    return register_callback_to_all_agents(agents, [callback])


def _add_callbacks_to_agent(
    agent: LlmAgent, callbacks: List[_SingleAfterToolCallback]
) -> bool:
    """Add multiple callbacks to a single agent, avoiding duplicates."""
    try:
        # Get existing callbacks
        existing_callbacks = []
        if agent.after_tool_callback:
            if isinstance(agent.after_tool_callback, list):
                existing_callbacks = agent.after_tool_callback.copy()
            else:
                existing_callbacks = [agent.after_tool_callback]

        # Add new callbacks, but avoid duplicates
        existing_callback_names = set()
        for cb in existing_callbacks:
            if hasattr(cb, "__name__"):
                existing_callback_names.add(cb.__name__)

        callbacks_added = 0
        for new_callback in callbacks:
            callback_name = getattr(new_callback, "__name__", str(new_callback))
            if callback_name not in existing_callback_names:
                existing_callbacks.append(new_callback)
                existing_callback_names.add(callback_name)
                callbacks_added += 1
            else:
                print(
                    f"Skipping duplicate callback '{callback_name}' for agent '{agent.name}'"
                )

        agent.after_tool_callback = existing_callbacks

        if callbacks_added > 0:
            print(f"Added {callbacks_added} new callbacks to agent '{agent.name}'")

        return True
    except Exception as e:
        print(f"Error adding callbacks to agent {agent.name}: {e}")
        return False


def _add_callback_to_agent(agent: LlmAgent, callback: _SingleAfterToolCallback) -> bool:
    """Add single callback to a single agent."""
    return _add_callbacks_to_agent(agent, [callback])


def discover_all_agents(
    root_agent: BaseAgent, context: Optional[ReadonlyContext] = None
) -> List[BaseAgent]:
    """Synchronously discover all agents by running async operations in a separate thread.

    This function provides a synchronous interface but handles async toolsets internally
    by creating a new event loop in a separate thread. Works in any context.

    Args:
        root_agent: The root agent to start discovery from
        context: Optional context for tool resolution

    Returns:
        List of all discovered agents including root, sub-agents, and tool agents

    Note:
        This will block for network I/O operations (MCPToolset, ToolboxToolset, etc.)
        but provides a synchronous interface. Uses ThreadPoolExecutor to avoid
        event loop conflicts.
    """
    import asyncio
    import concurrent.futures

    def run_async_in_thread():
        """Run the async discovery in a new thread with its own event loop."""
        # Create new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Run the async function
            return loop.run_until_complete(
                discover_all_agents_async(root_agent, context)
            )
        finally:
            # Clean up the loop
            loop.close()

    # Execute in thread pool to avoid event loop conflicts
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_async_in_thread)
        return future.result()  # This blocks until completion


def extract_tools_from_agent(agent) -> Dict[str, Any]:
    """Extract all tools from an agent instance and create a name->tool mapping.

    Args:
        agent: Agent instance to extract tools from

    Returns:
        Dictionary mapping tool names to tool objects
    """
    available_tools = {}

    if hasattr(agent, "tools") and agent.tools:
        for tool in agent.tools:
            tool_name = None
            tool_obj = None

            if hasattr(tool, "name"):
                tool_name = tool.name
                tool_obj = tool
            elif hasattr(tool, "__name__"):
                tool_name = tool.__name__
                tool_obj = tool
            elif hasattr(tool, "func") and hasattr(tool.func, "__name__"):
                tool_name = tool.func.__name__
                tool_obj = tool
            elif callable(tool):
                tool_name = getattr(tool, "__name__", str(tool))
                tool_obj = tool

            if tool_name and tool_obj:
                available_tools[tool_name] = tool_obj

    return available_tools


def _copy_agent_with_updated_model(base_agent_info, model_name: str):
    """
    Create a new agent instance with a specific model, based on an existing agent.

    Args:
        base_agent_info: EnsembleAgentInfo object containing the base agent
        model_name: The model name to use (e.g., "anthropic/claude-sonnet-4")

    Returns:
        New LlmAgent instance with the specified model
    """
    if not base_agent_info.agent_instance or not isinstance(
        base_agent_info.agent_instance, LlmAgent
    ):
        raise ValueError(
            f"Base agent must be an LlmAgent instance, got {type(base_agent_info.agent_instance)}"
        )

    base_agent = base_agent_info.agent_instance

    # Use the official copy method provided by BaseAgent (Pydantic model_copy)
    try:
        new_agent = base_agent.copy(
            update={
                "model": LiteLlm(model=model_name),
                "name": f"{base_agent.name}_{model_name.replace('/', '_').replace('-', '_')}",
            }
        )

        return new_agent

    except Exception as copy_error:
        # Fallback to manual creation if copy fails
        print(
            f"Warning: agent.copy() failed ({copy_error}), falling back to manual creation"
        )

        new_model = LiteLlm(model=model_name)

        # Create new agent with the same configuration but different model
        new_agent = LlmAgent(
            model=new_model,
            name=f"{base_agent.name}_{model_name.replace('/', '_').replace('-', '_')}",
            instruction=base_agent.instruction,
            description=base_agent.description
            or f"{base_agent.name} using {model_name}",
            tools=base_agent.tools,
            sub_agents=base_agent.sub_agents
            if hasattr(base_agent, "sub_agents")
            else None,
            # Copy additional configuration fields
            global_instruction=getattr(base_agent, "global_instruction", ""),
            generate_content_config=getattr(
                base_agent, "generate_content_config", None
            ),
            disallow_transfer_to_parent=getattr(
                base_agent, "disallow_transfer_to_parent", False
            ),
            disallow_transfer_to_peers=getattr(
                base_agent, "disallow_transfer_to_peers", False
            ),
            include_contents=getattr(base_agent, "include_contents", "default"),
        )

        # Copy ALL 4 types of callbacks (evidence: LlmAgent has 4 callback types)
        if (
            hasattr(base_agent, "before_model_callback")
            and base_agent.before_model_callback
        ):
            new_agent.before_model_callback = base_agent.before_model_callback

        if (
            hasattr(base_agent, "after_model_callback")
            and base_agent.after_model_callback
        ):
            new_agent.after_model_callback = base_agent.after_model_callback

        if (
            hasattr(base_agent, "before_tool_callback")
            and base_agent.before_tool_callback
        ):
            new_agent.before_tool_callback = base_agent.before_tool_callback

        if (
            hasattr(base_agent, "after_tool_callback")
            and base_agent.after_tool_callback
        ):
            new_agent.after_tool_callback = base_agent.after_tool_callback

        return new_agent
