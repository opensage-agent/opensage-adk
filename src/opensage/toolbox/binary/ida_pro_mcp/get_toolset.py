from google.adk.tools.mcp_tool.mcp_toolset import StreamableHTTPConnectionParams

from opensage.agents.opensage_agent import OpenSageMCPToolset
from opensage.toolbox.sandbox_requirements import requires_sandbox
from opensage.utils.agent_utils import get_mcp_host_and_port_from_session_id


@requires_sandbox("main")
def get_toolset(opensage_session_id: str) -> OpenSageMCPToolset:
    """Create a named MCP toolset for the IDA Pro MCP server."""
    host, port = get_mcp_host_and_port_from_session_id(
        "ida_pro_mcp", opensage_session_id
    )
    url = f"http://{host}:{port}/mcp"
    return OpenSageMCPToolset(
        name="ida_pro_mcp",
        connection_params=StreamableHTTPConnectionParams(
            url=url,
            timeout=30.0,
            sse_read_timeout=300.0,
        ),
        tool_name_prefix="ida_pro_mcp",
    )
