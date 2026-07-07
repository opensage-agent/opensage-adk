from google.adk.tools.mcp_tool.mcp_toolset import SseConnectionParams

from opensage.agents.opensage_agent import OpenSageMCPToolset
from opensage.toolbox.sandbox_requirements import requires_sandbox
from opensage.utils.agent_utils import get_mcp_url_from_session_id


@requires_sandbox("ghidra_mcp")
def get_toolset(opensage_session_id: str) -> OpenSageMCPToolset:
    """Create a named MCP toolset for the Ghidra MCP server."""
    url = get_mcp_url_from_session_id("ghidra_mcp", opensage_session_id)
    return OpenSageMCPToolset(
        name="ghidra_mcp",
        connection_params=SseConnectionParams(url=url),
        tool_name_prefix="ghidra_mcp",
    )
