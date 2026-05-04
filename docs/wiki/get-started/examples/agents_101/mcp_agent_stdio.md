# Agent with a Stdio MCP Server

**Source:** [`examples/agents_101/mcp_agent_local`](https://github.com/opensage-agent/opensage-adk/tree/main/examples/agents_101/mcp_agent_local)

Same shape as [`mcp_agent_sse`](./mcp_agent_sse.md), but starts the MCP server **locally over stdio** instead of SSE. No sidecar process is needed; `MCPToolset` launches `npx @modelcontextprotocol/server-filesystem` as a child process when the agent starts.

## Agent Source Code

```python title="examples/agents_101/mcp_agent_local/agent.py"
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool.mcp_toolset import (
    MCPToolset,
    StdioConnectionParams,
    StdioServerParameters,
)

from opensage.agents.opensage_agent import OpenSageAgent

root_agent = OpenSageAgent(
    name="mcp_client_agent",
    model=LiteLlm(model="anthropic/claude-opus-4-7"),
    instruction="You can use filesystem tools.",
    tools=[
        MCPToolset(
            connection_params=StdioConnectionParams(
                server_params=StdioServerParameters(
                    command="npx",
                    args=[
                        "-y",
                        "@modelcontextprotocol/server-filesystem",
                        "/path/to/your/workspace",
                    ],
                ),
            ),
        )
    ],
)
```
!!! note
    - Place the path in `args` with a directory on your host that the agent should be allowed to read/write.
    - Requires `npx` (ships with Node.js) on your `PATH`.


## Run It

```bash
uv run opensage web \
  --agent examples/agents_101/mcp_agent_local \
  --port 8000
```
