# Agent with an SSE MCP Server

**Source:** [`examples/agents_101/mcp_agent`](https://github.com/opensage-agent/opensage-adk/tree/main/examples/agents_101/mcp_agent)

Connect an `OpenSageAgent` to an MCP server using SSE protocol. The example bundles an `fs_server.sh` helper that launches the official filesystem MCP server through `supergateway` on port `3001`.

## Agent Source Code

```python title="examples/agents_101/mcp_agent/agent.py"
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseConnectionParams

from opensage.agents.opensage_agent import OpenSageAgent

root_agent = OpenSageAgent(
    name="mcp_client_agent",
    model=LiteLlm(model="anthropic/claude-opus-4-7"),
    instruction="You can use filesystem tools.",
    tools=[
        MCPToolset(
            connection_params=SseConnectionParams(url="http://127.0.0.1:3001/sse")
        )
    ],
)
```

The bundled `fs_server.sh` script (see the [source repo](https://github.com/opensage-agent/opensage-adk/tree/main/examples/agents_101/mcp_agent)) starts the MCP filesystem server and exposes it over SSE.

## Run It

Two processes are required: the MCP server and the agent.

**Terminal 1**: launch the filesystem MCP server:

```bash
cd examples/agents_101/mcp_agent
./fs_server.sh
```

!!! info
    Edit the path inside `fs_server.sh` to point at a directory you want the agent to browse.

**Terminal 2**: run the agent:

```bash
uv run opensage web \
  --agent examples/agents_101/mcp_agent \
  --port 8000
```

Open [http://localhost:8000](http://localhost:8000) and try asking agent to manage files through the MCP server.
