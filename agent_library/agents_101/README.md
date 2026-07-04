# OpenSage Examples 101

Small agents that each demonstrate one OpenSage-ADK feature. Run any of them with `opensage web --agent <path>`, then open the printed URL and chat with the agent. For a full walk-through of each, see the [Examples documentation](https://docs.adk.opensage-agent.ai/get-started/examples/).

## Examples

### sample_agent

A basic agent that exposes a Python function as a tool.

```bash
opensage web --agent agent_library/agents_101/sample_agent
```

### sample_agent_tool

A root agent that declares a calculation sub-agent through `subagents=[...]` and delegates to it with `call_subagent`.

```bash
opensage web --agent agent_library/agents_101/sample_agent_tool
```

### mcp_agent_sse

Connects to an MCP filesystem server over SSE. Start the server first, then the agent.

```bash
# Terminal 1: start the MCP server
./agent_library/agents_101/mcp_agent_sse/fs_server.sh

# Terminal 2: run the agent
opensage web --agent agent_library/agents_101/mcp_agent_sse
```

### mcp_agent_stdio

Connects to an MCP server over stdio; the agent launches the server as a subprocess, so no separate terminal is needed.

```bash
opensage web --agent agent_library/agents_101/mcp_agent_stdio
```

### mcp_server_tool

Mixes a Python function tool with a custom MCP server (`my_mcp_server.py`) over streamable HTTP.

```bash
# Terminal 1: start the custom MCP server
python agent_library/agents_101/mcp_server_tool/my_mcp_server.py

# Terminal 2: run the agent
opensage web --agent agent_library/agents_101/mcp_server_tool
```
