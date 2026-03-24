from google.adk.tools.mcp_tool.mcp_toolset import SseConnectionParams

from opensage.agents.opensage_agent import OpenSageMCPToolset
from opensage.toolbox.sandbox_requirements import requires_sandbox
from opensage.utils.agent_utils import get_mcp_url_from_session_id


@requires_sandbox("pdb_mcp")
def get_toolset(opensage_session_id: str) -> OpenSageMCPToolset:
    """Create a named MCP toolset for the PDB MCP server.

    Args:
        opensage_session_id (str): OpenSage session ID used to resolve the MCP SSE URL.
    Returns:
        OpenSageMCPToolset: OpenSageMCPToolset connected to the PDB MCP server (SSE).

        The returned toolset has:
        - name="pdb_mcp" so it can be selected by `create_subagent` via
          `tools_list=["pdb_mcp"]`.
        - tool_name_prefix="pdb_mcp" so expanded MCP tool names are prefixed.
    """
    url = get_mcp_url_from_session_id("pdb_mcp", opensage_session_id)
    mcp_toolset = OpenSageMCPToolset(
        name="pdb_mcp",
        connection_params=SseConnectionParams(url=url),
        tool_name_prefix="pdb_mcp",
    )
    return mcp_toolset
