from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.llm_agent import LlmAgent, _SingleAfterToolCallback
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models.base_llm import BaseLlm
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.tool_context import ToolContext

from opensage.config.config_dataclass import OpenSageConfig
from opensage.session.joern_client import JoernClient

INHERIT_MODEL = "inherit"


def get_model_from_agent(agent: Any) -> Optional[BaseLlm]:
    """Best-effort extraction of a usable model object from an agent."""
    if agent is None:
        return None
    canonical_model = getattr(agent, "canonical_model", None)
    if canonical_model is not None:
        return canonical_model
    model = getattr(agent, "model", None)
    if isinstance(model, BaseLlm):
        return model
    return None


def resolve_model_spec(
    model_name: str, *, tool_context: Optional[ToolContext] = None
) -> BaseLlm:
    """Resolve a model spec into a model instance.

        Supports a special sentinel value INHERIT_MODEL which reuses the current
        agent's model in the provided tool_context.

    Raises:
      ValueError: Raised when this operation fails."""
    if model_name == INHERIT_MODEL:
        if tool_context is None:
            raise ValueError("tool_context is required when model_name='inherit'")
        inv_context = getattr(tool_context, "_invocation_context", None)
        current_agent = getattr(inv_context, "agent", None)
        model = get_model_from_agent(current_agent)
        if model is None:
            raise ValueError("Unable to resolve current agent model for 'inherit'")
        return model
    return LiteLlm(model=model_name)


def get_opensage_session_from_context(
    context: InvocationContext | ToolContext,
):
    """Get OpenSage session from context using new OpenSageSession architecture."""
    # Lazy import to avoid circular dependency
    from opensage.session import get_opensage_session

    opensage_session_id = get_opensage_session_id_from_context(context)
    opensage_session = get_opensage_session(opensage_session_id)
    return opensage_session


def get_opensage_config_from_context(
    context: InvocationContext | ToolContext,
) -> OpenSageConfig:
    """Get OpenSage config from context using new OpenSageSession architecture."""
    # Lazy import to avoid circular dependency
    from opensage.session import get_opensage_session

    opensage_session_id = get_opensage_session_id_from_context(context)
    opensage_session = get_opensage_session(opensage_session_id)
    return opensage_session.config


def get_mcp_url_from_session_id(mcp_name: str, opensage_session_id: str) -> str:
    """Get MCP service URL from OpenSage session configuration.

    Args:
        mcp_name (str): Name of the MCP service (e.g., "gdb_mcp", "pdb_mcp")
        opensage_session_id (str): OpenSage session ID to retrieve configuration
    Returns:
        str: MCP SSE URL (e.g., "http://localhost:8000/sse")

    Raises:
        RuntimeError: If MCP service is not configured in the session

    Example::

        url = get_mcp_url_from_session_id("gdb_mcp", session_id)
        # Returns: "http://localhost:8000/sse"
    """
    # Lazy import to avoid circular dependency
    from opensage.session import get_opensage_session

    # Get session and configuration
    opensage_session = get_opensage_session(opensage_session_id)

    # Get MCP configuration
    mcp_config = opensage_session.config.mcp.services.get(mcp_name)
    if not mcp_config:
        raise RuntimeError(f"{mcp_name} not configured in mcp.services")

    host = mcp_config.sse_host
    port = mcp_config.sse_port
    url = f"http://{host}:{port}/sse"

    return url


def get_mcp_host_and_port_from_session_id(
    mcp_name: str, opensage_session_id: str
) -> tuple[str, int]:
    """Get MCP host and port from OpenSage session configuration.

    Raises:
      RuntimeError: Raised when this operation fails."""
    # Lazy import to avoid circular dependency
    from opensage.session import get_opensage_session

    opensage_session = get_opensage_session(opensage_session_id)

    # Get MCP configuration
    mcp_config = opensage_session.config.mcp.services.get(mcp_name)
    if not mcp_config:
        raise RuntimeError(f"{mcp_name} not configured in mcp.services")

    host = opensage_session.config.default_host
    port = mcp_config.sse_port
    return host, port


def get_sandbox_from_context(
    context: InvocationContext | ToolContext, sandbox_type: str = "main"
):
    """Get sandbox from context using OpenSageSession architecture.

    This is a convenience helper for tools that need to access sandboxes.
    It extracts the session ID from context and retrieves the appropriate sandbox.

    Args:
        context (InvocationContext | ToolContext): Tool or invocation context
        sandbox_type (str): Type of sandbox to retrieve (e.g., "main", "gdb_mcp", "neo4j")
    Returns:
        The requested sandbox instance

    Example::

        from opensage.toolbox.sandbox_requirements import requires_sandbox
        from opensage.utils.agent_utils import get_sandbox_from_context

        @requires_sandbox("main")
        async def bash_tool(command: str, context: ToolContext) -> str:
            sandbox = get_sandbox_from_context(context, "main")
            return await sandbox.run_command_in_container(command)
    """
    # Lazy import to avoid circular dependency
    from opensage.session import get_opensage_session

    opensage_session_id = get_opensage_session_id_from_context(context)
    opensage_session = get_opensage_session(opensage_session_id)
    return opensage_session.sandboxes.get_sandbox(sandbox_type)


def save_content_to_sandbox_file(
    context: "InvocationContext | ToolContext",
    content: str,
    tool_name: str,
    output_dir: str = "/workspace/.tool_outputs",
    sandbox_type: str = "main",
) -> Optional[str]:
    """Save content to a file in the sandbox and return the file path.

    This is a shared utility for saving long tool outputs to files in the sandbox,
    allowing agents to reference the file path later if needed.

    Args:
        context ('InvocationContext | ToolContext'): Tool or invocation context for sandbox access.
        content (str): The content to save.
        tool_name (str): Name of the tool (used in filename).
        output_dir (str): Directory in sandbox to save files.
        sandbox_type (str): Type of sandbox to use.
    Returns:
        Optional[str]: File path if saved successfully, None otherwise.
    """
    import logging
    import uuid

    logger = logging.getLogger(__name__)

    try:
        logger.warning(
            f"[save_content_to_sandbox_file] Starting save:\n"
            f"  tool_name: {tool_name}\n"
            f"  output_dir: {output_dir}\n"
            f"  content_length: {len(content)} chars\n"
            f"  sandbox_type: {sandbox_type}"
        )

        sandbox = get_sandbox_from_context(context, sandbox_type)
        file_id = uuid.uuid4().hex[:8]
        output_file = f"{output_dir}/{tool_name}_{file_id}.txt"

        logger.warning(f"[save_content_to_sandbox_file] Target file: {output_file}")

        # Create directory if not exists
        mkdir_result = sandbox.run_command_in_container(
            f"mkdir -p {output_dir}", timeout=10
        )
        logger.warning(f"[save_content_to_sandbox_file] mkdir result: {mkdir_result}")

        # Use heredoc to write content safely
        write_result = sandbox.run_command_in_container(
            f"cat > {output_file} << 'OPENSAGE_SAVE_EOF'\n{content}\nOPENSAGE_SAVE_EOF",
            timeout=30,
        )
        logger.warning(f"[save_content_to_sandbox_file] write result: {write_result}")

        # Verify file was created
        verify_result = sandbox.run_command_in_container(
            f"ls -la {output_file} && wc -c {output_file}",
            timeout=10,
        )
        logger.warning(
            f"[save_content_to_sandbox_file] File verification: {verify_result}"
        )

        logger.warning(f"[save_content_to_sandbox_file] SUCCESS saved to {output_file}")
        return output_file

    except Exception as e:
        logger.error(
            f"[save_content_to_sandbox_file] FAILED to save content:\n"
            f"  tool_name: {tool_name}\n"
            f"  error: {e}",
            exc_info=True,
        )
        return None


async def get_neo4j_client_from_context(
    context: InvocationContext | ToolContext, client_type: str = "history"
):
    """Get Neo4j client from context using new OpenSageSession architecture.

    Args:
        context (InvocationContext | ToolContext): Tool or invocation context
        client_type (str): Type of client ("history", "analysis", etc.)
    Returns:
        Neo4j client for the specified type
    """
    # Lazy import to avoid circular dependency
    from opensage.session import get_opensage_session

    opensage_session_id = get_opensage_session_id_from_context(context)
    opensage_session = get_opensage_session(opensage_session_id)
    return await opensage_session.neo4j.get_async_client(client_type)


async def get_joern_client_from_context(
    context: InvocationContext | ToolContext,
) -> JoernClient:
    """Get Joern client from context using new OpenSageSession architecture.

    Args:
        context (InvocationContext | ToolContext): Tool or invocation context
    Returns:
        JoernClient: JoernClient instance
    """
    # Lazy import to avoid circular dependency
    from opensage.session import get_opensage_session

    opensage_session_id = get_opensage_session_id_from_context(context)
    opensage_session = get_opensage_session(opensage_session_id)
    joern_port = 18087
    return JoernClient(
        server_endpoint=f"{opensage_session.config.default_host}:{joern_port}"
    )


def get_opensage_session_id_from_context(context) -> str:
    """
    Extract opensage_session_id from context (ToolContext, InvocationContext, or similar).

    This is a unified utility function used across the OpenSage Framework to consistently
    extract and manage opensage_session_id for session isolation.

    Args:
        context: Any context object that might contain session information
    Returns:
        str: The opensage_session_id for session isolation
    """
    # Try to get from context.state first (immediate access)
    if hasattr(context, "state") and hasattr(context.state, "get"):
        opensage_session_id = context.state.get("opensage_session_id")
        if opensage_session_id:
            return opensage_session_id

    # Get session from different context types
    session = None
    if hasattr(context, "_invocation_context") and hasattr(
        context._invocation_context, "session"
    ):
        session = context._invocation_context.session
    elif hasattr(context, "session"):
        session = context.session

    if session is not None:
        # Ensure opensage_session_id is set in session.state
        if "opensage_session_id" not in session.state:
            session.state["opensage_session_id"] = session.id

        # Also set it in context.state if possible for immediate access
        if hasattr(context, "state"):
            context.state["opensage_session_id"] = session.state["opensage_session_id"]

        return session.state["opensage_session_id"]

    # Ultimate fallback
    return "default"


def _discover_agents_recursive(
    agent: BaseAgent,
    agents: List[BaseAgent],
    discovered_names: Set[str],
) -> None:
    """Recursively discover agents (synchronous version, skips toolsets)."""
    # Avoid infinite loops by tracking agent names
    if agent.name in discovered_names:
        return

    discovered_names.add(agent.name)
    agents.append(agent)

    # 1. Discover sub-agents
    for sub_agent in agent.sub_agents:
        _discover_agents_recursive(sub_agent, agents, discovered_names)

    # 2. Discover agents in tools (only for LlmAgent)
    if isinstance(agent, LlmAgent):
        # Directly access agent.tools without canonical_tools to avoid async
        # Only process AgentTool instances, skip callables and toolsets
        for tool_union in agent.tools:
            if isinstance(tool_union, AgentTool):
                _discover_agents_recursive(tool_union.agent, agents, discovered_names)


def register_callback_to_all_agents(
    agents: List[BaseAgent], callbacks: List[_SingleAfterToolCallback]
) -> Dict[str, bool]:
    """Register multiple after_tool_callbacks to all agents.

    Args:
        agents (List[BaseAgent]): List of agents to register callbacks to
        callbacks (List[_SingleAfterToolCallback]): List of callback functions to register
    Returns:
        Dict[str, bool]: Dict mapping agent names to registration success status
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


def discover_all_agents(
    root_agent: BaseAgent, context: Optional[ReadonlyContext] = None
) -> List[BaseAgent]:
    """Discover all agents without creating temporary event loops.

    This function discovers sub-agents and AgentTools only. Toolsets (like MCPToolset)
    are intentionally skipped to avoid event loop conflicts.

    Args:
        root_agent (BaseAgent): The root agent to start discovery from
        context (Optional[ReadonlyContext]): Optional context (unused, kept for API compatibility)
    Returns:
        List[BaseAgent]: List of all discovered agents including root, sub-agents, and agents in AgentTools

    Note:
        This is a lightweight synchronous operation that doesn't trigger network I/O.
        Toolsets will be expanded later when the agent actually runs in the main event loop.
    """
    discovered_names: Set[str] = set()
    agents: List[BaseAgent] = []
    _discover_agents_recursive(root_agent, agents, discovered_names)
    return agents


def extract_tools_from_agent(agent) -> Dict[str, Any]:
    """Extract all tools from an agent instance and create a name->tool mapping.

    Args:
        agent: Agent instance to extract tools from
    Returns:
        Dict[str, Any]: Dictionary mapping tool names to tool objects
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
        Create a new OpenSageAgent instance with a specific model, based on an existing OpenSageAgent.

        Args:
            base_agent_info: EnsembleAgentInfo object containing the base agent (must be OpenSageAgent)
            model_name (str): The model name to use (e.g., "anthropic/claude-sonnet-4") or
              INHERIT_MODEL ("inherit") to reuse inherit_model.
            inherit_model: Model instance used when model_name==INHERIT_MODEL.

    Raises:
      ValueError: Raised when this operation fails.
        Returns:
            New OpenSageAgent instance with the specified model and same enabled_skills
    """
    # NOTE: This is intentionally a private helper, but used by ensemble manager.

    # pylint: disable=protected-access
    from opensage.agents.opensage_agent import OpenSageAgent

    if not base_agent_info.agent_instance or not isinstance(
        base_agent_info.agent_instance, OpenSageAgent
    ):
        raise ValueError(
            f"Base agent must be an OpenSageAgent instance, got {type(base_agent_info.agent_instance)}"
        )

    base_agent = base_agent_info.agent_instance

    # Get enabled_skills from the OpenSageAgent instance
    enabled_skills = getattr(base_agent, "_enabled_skills", None)

    if model_name == INHERIT_MODEL:
        raise ValueError(
            "model_name='inherit' requires using _copy_agent_with_updated_model_v2 "
            "with inherit_model provided"
        )

    # Use the official copy method provided by BaseAgent (Pydantic model_copy)
    try:
        new_agent = base_agent.copy(
            update={
                "model": LiteLlm(model=model_name),
                "name": f"{base_agent.name}_{model_name.replace('/', '_').replace('-', '_')}",
            }
        )

        # Copy enabled_skills attribute (copy() doesn't copy private attributes)
        new_agent._enabled_skills = enabled_skills

        # If enabled_skills exists, update the system prompt
        # (instruction was copied, but we need to regenerate tool_prompt if needed)
        # Actually, since we're copying, the instruction already has the tool_prompt
        # But we should ensure _enabled_skills is set correctly
        # The instruction should already be correct from the copy

        return new_agent

    except Exception as copy_error:
        # Fallback to manual creation if copy fails
        print(
            f"Warning: agent.copy() failed ({copy_error}), falling back to manual creation"
        )

        new_model = LiteLlm(model=model_name)

        # Create new OpenSageAgent with the same configuration but different model
        new_agent = OpenSageAgent(
            model=new_model,
            name=f"{base_agent.name}_{model_name.replace('/', '_').replace('-', '_')}",
            instruction=base_agent.instruction,
            description=base_agent.description
            or f"{base_agent.name} using {model_name}",
            tools=base_agent.tools,
            enabled_skills=enabled_skills,  # Pass enabled_skills from original agent
            sub_agents=base_agent.sub_agents
            if hasattr(base_agent, "sub_agents")
            else None,
            tool_combos=getattr(base_agent, "tool_combos", None),
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


def _copy_agent_with_updated_model_v2(
    base_agent_info, model_name: str, *, inherit_model: Optional[BaseLlm] = None
):
    """Like _copy_agent_with_updated_model but supports model inheritance.

    Raises:
      ValueError: Raised when this operation fails."""
    from opensage.agents.opensage_agent import OpenSageAgent

    if not base_agent_info.agent_instance or not isinstance(
        base_agent_info.agent_instance, OpenSageAgent
    ):
        raise ValueError(
            f"Base agent must be an OpenSageAgent instance, got {type(base_agent_info.agent_instance)}"
        )

    base_agent = base_agent_info.agent_instance
    enabled_skills = getattr(base_agent, "_enabled_skills", None)

    if model_name == INHERIT_MODEL:
        if inherit_model is None:
            raise ValueError("inherit_model must be provided for model_name='inherit'")
        resolved_model = inherit_model
        suffix = INHERIT_MODEL
    else:
        resolved_model = LiteLlm(model=model_name)
        suffix = model_name.replace("/", "_").replace("-", "_")

    try:
        new_agent = base_agent.copy(
            update={
                "model": resolved_model,
                "name": f"{base_agent.name}_{suffix}",
            }
        )
        new_agent._enabled_skills = enabled_skills
        return new_agent
    except Exception as copy_error:
        print(
            f"Warning: agent.copy() failed ({copy_error}), falling back to manual creation"
        )
        new_agent = OpenSageAgent(
            model=resolved_model,
            name=f"{base_agent.name}_{suffix}",
            instruction=base_agent.instruction,
            description=base_agent.description or f"{base_agent.name} using {suffix}",
            tools=base_agent.tools,
            enabled_skills=enabled_skills,
            sub_agents=base_agent.sub_agents
            if hasattr(base_agent, "sub_agents")
            else None,
            tool_combos=getattr(base_agent, "tool_combos", None),
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
        return new_agent
