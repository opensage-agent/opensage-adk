from __future__ import annotations

import os
import posixpath
import re
import shlex
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

_AGENT_NAME_FALLBACK = "agent"
_SANDBOX_WRITE_CHUNK_SIZE = 8192
_SAFE_FILENAME_PART_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def sanitize_agent_name(name: str) -> str:
    """Canonical sanitizer for agent names / app_names / session-store dir keys.

    Produces a valid Python identifier: letters, digits, and underscores only,
    never starting with a digit. This matches ADK's own ``BaseAgent.name``
    validator (``value.isidentifier()``), so sanitized names are safe for
    direct use as ADK agent names, app_names, and filesystem keys.

    Returns ``"agent"`` if the input collapses to an empty string.
    """
    sanitized = re.sub(r"[^A-Za-z0-9_]+", "_", (name or "").strip())
    sanitized = sanitized.strip("_") or _AGENT_NAME_FALLBACK
    if sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return sanitized


def _get_litellm_kwargs_for_model(model_name: str) -> Dict[str, Any]:
    """Build LiteLLM kwargs from environment for provider-specific routing.

    LiteLLM forwards these kwargs to ``litellm.acompletion()``. This allows
    OpenSage to honor local proxy endpoints such as ``ANTHROPIC_BASE_URL``
    without requiring every call site to thread ``api_base`` manually.
    """
    kwargs: Dict[str, Any] = {}
    if model_name.startswith("anthropic/"):
        api_base = os.getenv("ANTHROPIC_API_BASE") or os.getenv("ANTHROPIC_BASE_URL")
        if api_base:
            kwargs["api_base"] = api_base.rstrip("/")
    return kwargs


def create_litellm_model(model_name: str) -> LiteLlm:
    """Create a LiteLlm model with provider-specific env overrides applied."""
    return LiteLlm(model=model_name, **_get_litellm_kwargs_for_model(model_name))


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


def _sanitize_filename_part(raw: str | None, fallback: str) -> str:
    cleaned = _SAFE_FILENAME_PART_RE.sub("_", str(raw or "").strip()).strip("._-")
    return cleaned or fallback


def _sanitize_file_extension(raw: str | None) -> str:
    if not isinstance(raw, str) or not raw.startswith("."):
        return ".txt"
    suffix = _SAFE_FILENAME_PART_RE.sub("_", raw[1:].strip()).strip("._-")
    return f".{suffix}" if suffix else ".txt"


async def _write_text_file_in_sandbox(
    sandbox: Any,
    path: str,
    content: str,
    *,
    timeout: int = 30,
) -> tuple[bool, str]:
    parent_dir = posixpath.dirname(path) or "."
    quoted_parent = shlex.quote(parent_dir)
    quoted_path = shlex.quote(path)

    output, exit_code = await sandbox.arun_command_in_container(
        f"mkdir -p {quoted_parent} && rm -f {quoted_path} && : > {quoted_path}",
        timeout=timeout,
    )
    if exit_code != 0:
        return False, f"create failed ({exit_code}): {output.strip()}"

    for start in range(0, len(content), _SANDBOX_WRITE_CHUNK_SIZE):
        chunk = content[start : start + _SANDBOX_WRITE_CHUNK_SIZE]
        output, exit_code = await sandbox.arun_command_in_container(
            f"printf %s {shlex.quote(chunk)} >> {quoted_path}",
            timeout=timeout,
        )
        if exit_code != 0:
            return False, f"write failed ({exit_code}): {output.strip()}"

    output, exit_code = await sandbox.arun_command_in_container(
        f"chmod 0644 {quoted_path}", timeout=timeout
    )
    if exit_code != 0:
        return False, f"chmod failed ({exit_code}): {output.strip()}"

    output, exit_code = await sandbox.arun_command_in_container(
        f"test -f {quoted_path} && wc -c < {quoted_path}", timeout=10
    )
    if exit_code != 0:
        return False, f"verify failed ({exit_code}): {output.strip()}"

    try:
        actual_size = int(output.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return False, f"verify returned non-byte-count output: {output.strip()}"

    expected_size = len(content.encode("utf-8"))
    if actual_size != expected_size:
        return (
            False,
            f"verify byte count mismatch: expected {expected_size}, got {actual_size}",
        )

    return True, f"{actual_size} bytes"


async def save_content_to_sandbox_file(
    context: "InvocationContext | ToolContext",
    content: str,
    tool_name: str,
    output_dir: str = "/workspace/.tool_outputs",
    sandbox_type: str = "main",
    file_id: Optional[str] = None,
    file_extension: str = ".txt",
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
        file_id (Optional[str]): Optional stable file id for the output filename.
        file_extension (str): Output file extension including the leading dot.
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
        safe_tool_name = _sanitize_filename_part(tool_name, "tool")
        resolved_file_id = _sanitize_filename_part(file_id, uuid.uuid4().hex[:8])
        resolved_extension = _sanitize_file_extension(file_extension)
        output_file = (
            f"{output_dir.rstrip('/')}/"
            f"{safe_tool_name}_{resolved_file_id}{resolved_extension}"
        )

        logger.warning(f"[save_content_to_sandbox_file] Target file: {output_file}")

        saved, detail = await _write_text_file_in_sandbox(sandbox, output_file, content)
        if not saved:
            logger.error(
                "[save_content_to_sandbox_file] FAILED to save content to %s: %s",
                output_file,
                detail,
            )
            return None

        logger.warning(
            "[save_content_to_sandbox_file] SUCCESS saved to %s (%s)",
            output_file,
            detail,
        )
        return output_file

    except Exception as e:
        logger.exception(
            f"[save_content_to_sandbox_file] FAILED to save content:\n"
            f"  tool_name: {tool_name}\n"
            f"  error: {e}",
        )
        return None


async def get_neo4j_client_from_context(
    context: InvocationContext | ToolContext, client_type: str = "analysis"
):
    """Get Neo4j client from context using new OpenSageSession architecture.

    Args:
        context (InvocationContext | ToolContext): Tool or invocation context
        client_type (str): Type of client ("analysis", "default", or a custom name)
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
