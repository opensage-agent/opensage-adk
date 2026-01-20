from google.adk.tools.mcp_tool.mcp_toolset import SseConnectionParams

from aigise.agents.aigise_agent import AigiseMCPToolset
from aigise.toolbox.decorators import requires_sandbox, safe_tool_execution
from aigise.utils.agent_utils import get_mcp_url_from_session_id


@safe_tool_execution
@requires_sandbox("gdb_mcp")
def get_toolset(aigise_session_id: str) -> AigiseMCPToolset:
    """Create a named MCP toolset for the GDB MCP server.

    Args:
        aigise_session_id: AIgiSE session ID used to resolve the MCP SSE URL.

    Returns:
        AigiseMCPToolset connected to the GDB MCP server (SSE).

        The returned toolset has:
        - name="gdb_mcp" so it can be selected by `create_subagent` via
          `tools_list=["gdb_mcp"]`.
        - tool_name_prefix="gdb_mcp" so expanded MCP tool names are prefixed
          (e.g. "gdb_mcp_step_control") to avoid collisions.
    """
    url = get_mcp_url_from_session_id("gdb_mcp", aigise_session_id)
    mcp_toolset = AigiseMCPToolset(
        name="gdb_mcp",
        connection_params=SseConnectionParams(url=url),
        tool_name_prefix="gdb_mcp",
    )
    return mcp_toolset
