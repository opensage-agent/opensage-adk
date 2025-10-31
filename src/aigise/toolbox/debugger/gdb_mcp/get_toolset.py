from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseConnectionParams

from aigise.toolbox.decorators import requires_sandbox, safe_tool_execution
from aigise.utils.agent_utils import get_mcp_url_from_session_id


@safe_tool_execution
@requires_sandbox("gdb_mcp")
def get_toolset(aigise_session_id: str) -> MCPToolset:
    """Create MCPToolset with GDB MCP server running in Docker container.

    Args:
        aigise_session_id: Shared session ID for session-based management

    Returns:
        MCPToolset connected to GDB MCP server
    """
    url = get_mcp_url_from_session_id("gdb_mcp", aigise_session_id)
    mcp_toolset = MCPToolset(connection_params=SseConnectionParams(url=url))
    return mcp_toolset
