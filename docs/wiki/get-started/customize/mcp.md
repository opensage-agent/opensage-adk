# MCP Tools

**Section:** `[mcp]`

Tools are the verbs your agent can use. OpenSage ships a toolbox of Python function tools (no configuration needed to use them) plus support for **Model Context Protocol** servers that expose external capabilities like debugging, filesystem access, or custom services. The `[mcp]` section is where you wire in those MCP servers over Server-Sent Events. OpenSage ships built-in definitions for `gdb_mcp` and `pdb_mcp`; any custom MCP server that follows the protocol can be added the same way.

## Defining a Service

Each service lives under `[mcp.services.<name>]`. A port is the one required field; the host defaults to the root-level `default_host` (or `127.0.0.1` if unset).

```toml title="config.toml"
[mcp.services.gdb_mcp]
sse_port = 1111

[mcp.services.pdb_mcp]
sse_port = 1112
sse_host = "localhost"
```

## How MCP Services Relate to Sandboxes

An MCP service is the *endpoint*; the server implementing it usually runs in its own sandbox. Pair `[mcp.services.<name>]` with `[sandbox.sandboxes.<name>]`; the sandbox provides the container and port binding, and this section tells OpenSage where to reach it.

See the [Agent with an SSE MCP Server](../examples/agents_101/mcp_agent_sse.md), [Agent with a Stdio MCP Server](../examples/agents_101/mcp_agent_stdio.md), and [Agent with a Custom MCP Server](../examples/agents_101/mcp_server_tool.md) examples for complete setups.

---

See the [`[mcp]` field reference](../../reference/configuration/mcp.md) for the full list of per-service fields.
