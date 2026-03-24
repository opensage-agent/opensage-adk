from google.adk.tools.mcp_tool.mcp_toolset import SseConnectionParams

from opensage.agents.opensage_agent import OpenSageMCPToolset
from opensage.toolbox.sandbox_requirements import requires_sandbox
from opensage.utils.agent_utils import get_mcp_url_from_session_id


@requires_sandbox("gdb_mcp")
def get_toolset(opensage_session_id: str) -> OpenSageMCPToolset:
    """Create a named MCP toolset for the GDB MCP server.

    Args:
        opensage_session_id (str): OpenSage session ID used to resolve the MCP SSE URL.
    Returns:
        OpenSageMCPToolset: OpenSageMCPToolset connected to the GDB MCP server (SSE).

        The returned toolset has:
        - name="gdb_mcp" so it can be selected by `create_subagent` via
          `tools_list=["gdb_mcp"]`.
        - tool_name_prefix="gdb_mcp" so expanded MCP tool names are prefixed
          (e.g. "gdb_mcp_step_control") to avoid collisions.
    """
    url = get_mcp_url_from_session_id("gdb_mcp", opensage_session_id)
    mcp_toolset = OpenSageMCPToolset(
        name="gdb_mcp",
        connection_params=SseConnectionParams(url=url),
        tool_name_prefix="gdb_mcp",
    )
    return mcp_toolset
