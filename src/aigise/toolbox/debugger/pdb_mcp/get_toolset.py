from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseConnectionParams

from aigise.toolbox.decorators import requires_sandbox
from aigise.utils.agent_utils import get_mcp_url_from_session_id


@requires_sandbox("pdb_mcp")
def get_toolset(aigise_session_id: str) -> MCPToolset:
    """Create MCPToolset with PDB MCP server running in Docker container.

    Args:
        shared_session_id: Shared session ID for session-based management

    Returns:
        MCPToolset connected to PDB MCP server
    """
    url = get_mcp_url_from_session_id("pdb_mcp", aigise_session_id)
    mcp_toolset = MCPToolset(connection_params=SseConnectionParams(url=url))
    return mcp_toolset
