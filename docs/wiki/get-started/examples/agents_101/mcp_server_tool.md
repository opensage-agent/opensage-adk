# Agent with a Custom MCP Server

**Source:** [`agent_library/agents_101/mcp_server_tool`](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents_101/mcp_server_tool)

Combines a regular Python function tool with a **custom MCP server** exposed over streamable HTTP. Useful when you want to author the MCP server yourself (for example, to wrap an existing service).

The example ships two files:

- `agent.py`: the `OpenSageAgent` that connects to the MCP server.
- [`my_mcp_server.py`](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents_101/mcp_server_tool/my_mcp_server.py): a minimal MCP server that exposes a `calculate_multiplication` tool via `StreamableHTTPSessionManager`.

## Agent Source Code

```python title="agent_library/agents_101/mcp_server_tool/agent.py"
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool.mcp_toolset import (
    MCPToolset,
    StreamableHTTPConnectionParams,
)

from opensage.agents.opensage_agent import OpenSageAgent


def calculate_add(a: float, b: float) -> float:
    """Calculate the sum of two numbers."""
    return a + b


root_agent = OpenSageAgent(
    model=LiteLlm(model="anthropic/claude-opus-4-7"),
    name="simple_math_agent",
    description="A simple math agent that can perform addition operations.",
    instruction="You are a helpful math assistant.",
    tools=[
        calculate_add,
        MCPToolset(
            connection_params=StreamableHTTPConnectionParams(
                url="http://0.0.0.0:9998/mcp",
            ),
        ),
    ],
)
```

## Run It

**Terminal 1**: start the custom MCP server on port `9998`:

```bash
uv run python agent_library/agents_101/mcp_server_tool/my_mcp_server.py
```

**Terminal 2**: run the agent:

```bash
uv run opensage web \
  --agent agent_library/agents_101/mcp_server_tool \
  --port 8000
```

The agent will expose both `calculate_add` (local Python tool) and `calculate_multiplication` (from the MCP server).

Open [http://localhost:8000](http://localhost:8000) and try asking the agent to do both addition and multiplication.
