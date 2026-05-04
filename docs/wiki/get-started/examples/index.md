---
icon: lucide/layers
---

# Agent Examples

This section demonstrates some runnable agents using OpenSage-ADK.


## `agents_101/`: Common Agent Patterns

- [`sample_agent`](./agents_101/sample_agent.md): Single `OpenSageAgent` with one Python function tool.
- [`sample_agent_tool`](./agents_101/sample_agent_tool.md): Root agent that calls a sub-agent wrapped as an `AgentTool`.
- [`mcp_agent_sse`](./agents_101/mcp_agent_sse.md): Connect to a remote MCP server over SSE.
- [`mcp_agent_stdio`](./agents_101/mcp_agent_stdio.md): Launch a local MCP server over stdio.
- [`mcp_server_tool`](./agents_101/mcp_server_tool.md): Mix a Python tool with a custom MCP server (streamable HTTP).

## `agents_with_features/`: Agents with OpenSage-ADK Features

- [`sample_agent_ensemble`](./agents_with_features/sample_agent_ensemble.md): Run the same prompt across multiple models and aggregate results.
- [`sample_dynamic_subagent`](./agents_with_features/sample_dynamic_subagent.md): Root agent spawns specialized sub-agents at runtime.
- [`sample_tool_combo`](./agents_with_features/sample_tool_combo.md): Chain tools as a single step with `ToolCombo`.
- [`sample_summarization`](./agents_with_features/sample_summarization.md): Register history-summarization callbacks on every agent.
- [`sample_neo4j_logging`](./agents_with_features/sample_neo4j_logging.md): Persist agent history in a Neo4j graph.
- [`sample_web_search`](./agents_with_features/sample_web_search.md): Provider-side web search via `WebSearchTool`, plus a Gemini + Google Search variant.


The [`examples/`](https://github.com/opensage-agent/opensage-adk/tree/main/examples) directory ships runnable agents grouped by complexity. Use them as starting points when building your own.
