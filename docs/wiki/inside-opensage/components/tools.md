# Tools

Tools are the **verbs** your agent can use. OpenSage-ADK accepts tools in several shapes (plain Python functions, ADK `BaseToolset` instances, MCP toolsets, and bash-scripted "skills") and normalizes them into one uniform list before the model sees anything.

Source:

- Agent entry point: [`src/opensage/agents/opensage_agent.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/agents/opensage_agent.py)
- Normalization: [`src/opensage/toolbox/tool_normalization.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/toolbox/tool_normalization.py)
- Sandbox-dependency decorator: [`src/opensage/toolbox/sandbox_requirements.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/toolbox/sandbox_requirements.py)
- Bash skills: [`src/opensage/bash_tools/`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/bash_tools)

## The Shapes You Can Pass as `tools=[...]`

| Shape | Example | What it is |
|---|---|---|
| **Python function** | `calculate_add` | Sync or async function. Signature and docstring become the tool schema automatically. |
| **`BaseToolset`** | `GoogleSearchToolset()` | A batch of related tools that expand into individual entries. |
| **`MCPToolset` / `OpenSageMCPToolset`** | `get_gdb_toolset(session_id)` | A live MCP server connection; tools are discovered over the protocol. |

To attach another agent, declare it through `subagents=[...]` and call it with `call_subagent` rather than wrapping it in `AgentTool`. `OpenSageAgent` rejects an `AgentTool` placed in `tools=`. See [Multi-Agent](./multi-agent.md).

## Normalization: One Pipe in Front of the LLM

When you construct an `OpenSageAgent`, the `tools` list passes through `make_toollikes_safe_dict()` before the parent `LlmAgent.__init__` sees it. Every entry is wrapped so that:

- **Exceptions never escape.** A raised exception becomes `{"success": False, "error": "<traceback>"}`, so the LLM sees a structured failure instead of the run crashing.
- **Return values are dict-shaped.** Raw values are wrapped as `{"result": ...}`; dict-returning tools pass through unchanged. MCP tools (which already return structured `Content` objects) and long-running MCP tools bypass this to preserve their own shape.
- **Toolsets recurse.** `BaseToolset` instances are unfolded and each sub-tool is wrapped individually. MCP toolsets are passed through unchanged because they are resolved asynchronously.
- **Function identity is preserved.** Wrappers copy `__signature__`, `__doc__`, and any attributes the framework cares about (`__sandbox_requirements__`, etc.) via `_recreate_function_with_merged_globals()`.

## Sandbox-Dependency Declaration

A tool can declare which sandbox(es) it needs to run in:

```python
from opensage.toolbox.sandbox_requirements import requires_sandbox

@requires_sandbox("gdb_mcp", "main")
def step_into(tool_context, ...):
    ...
```

For toolset factories (the `get_*_toolset()` functions for MCP tools), the decorator attaches `__sandbox_requirements__` to the *returned* instance so dependencies travel with the toolset. At session startup, `collect_sandbox_dependencies()` walks the entire agent tree (tools, sub-agents, enabled skills) and builds the set of sandboxes that actually need to be launched. The `main` sandbox is always included; unused sandboxes declared in `config.toml` are pruned.

Skills declare the same thing in their `SKILL.md`:

```markdown title="SKILL.md"
## Requires Sandbox

- `neo4j`
- `joern`
```

## The Skills System

Alongside in-process Python tools, OpenSage ships a **bash skills** system: directories under `src/opensage/bash_tools/` (plus `~/.local/opensage/bash_tools/` for user-local extensions) where each skill is a folder with a `SKILL.md` documentation file and usually a `scripts/` directory of runnable programs. Each skill is surfaced to the LLM as one or more tool entries it can invoke through the `list_available_scripts` / `run_terminal_command` interface.

Control which skills an agent sees via `enabled_skills` on `OpenSageAgent`:

- `enabled_skills=None`: no skills loaded. Smallest surface.
- `enabled_skills="all"`: load every top-level skill (`<root>/*/SKILL.md`).
- `enabled_skills=["retrieval", "static_analysis/search-function"]`: each entry is a path prefix (recursive).

`ToolLoader` (in `opensage_agent.py`) reads each matching `SKILL.md` and generates a preamble inserted into the agent's system prompt, so the model knows what each skill directory offers without you repeating yourself in the instruction.

Built-in skill categories: `retrieval`, `static_analysis`, `coverage`, `fuzz`, `neo4j`, `mmp`, `new_tool_creator`, `diagnose`.

## MCP Integration

An MCP toolset is a connection to an out-of-process server that speaks [Model Context Protocol](https://modelcontextprotocol.io/). Factories like `get_gdb_toolset(session_id)` build an `OpenSageMCPToolset`: a thin subclass of ADK's `McpToolset` that adds a stable `name` and enforces `tool_name_prefix == name` (so prefixed tool names like `gdb_mcp_step_control` map back to the toolset unambiguously).

The workflow:

1. Your `config.toml` declares a sandbox that hosts the MCP server (`[sandbox.sandboxes.gdb_mcp]`) and an MCP service pointing at it (`[mcp.services.gdb_mcp] sse_port = 1111`).
2. When the sandbox initializer runs, it starts the MCP server process.
3. Readiness is polled via `_check_mcp_connections()` until the SSE endpoint responds.
4. At first tool-call, the toolset connects via `SseConnectionParams(url=get_mcp_url_from_session_id(...))` and discovers the available tools from the server.

Dynamic sub-agents can **inherit an MCP toolset by name**: `create_subagent(..., tools_list=["gdb_mcp"])` injects the whole toolset into the new agent if the parent has it.

## Built-In Tool Catalog

The general-purpose utility tools live under `src/opensage/toolbox/general/`:

- **`bash_tool.py`**: `bash_tool_main`, an escape hatch for any shell command in the main sandbox.
- **`bash_tools_interface.py`**: `run_terminal_command`, `list_available_scripts`, `list_background_tasks`, `get_background_task_output`. The surface through which bash skills are invoked, including long-running background tasks.
- **`fileop.py`**: `view_file`, `str_replace_edit`. LLM-friendly file read / edit primitives.
- **`web_search_tool.py`**: `WebSearchTool()`. Provider-side web search (Anthropic, OpenAI, xAI).
- **`agent_tools.py`**: `think`, `plan`, `complain`, `note_suspicious_things`, `log_finding`, `critique`, `audit_assumptions`, `validate_claim`. See [Multi-Agent: Self-Reflection Tools](./multi-agent.md#self-reflection-tools).
- **`orchestration_tools.py`**: `create_subagent`, `call_subagent`, `list_subagents`, `get_available_models`. See [Multi-Agent: Creating Sub-Agents Dynamically](./multi-agent.md#creating-sub-agents-dynamically).

Framework-level Python tools under `toolbox/`: `general/` (agent utilities, shell interfaces, file operations), `debugger/` (GDB/PDB MCP factories), `binary/` (Ghidra, IDA Pro, pyghidra MCP factories), and `finish_task/`. Domain workflows such as retrieval, static analysis, fuzzing, coverage, Neo4j, and MMP live under `src/opensage/bash_tools/` or benchmark-owned modules instead of `toolbox/`.

## Related References

- [Customize -> Add MCP Tools](../../get-started/customize/mcp.md): configuration guide.
- [`[mcp]` field reference](../../reference/configuration/mcp.md): per-service fields.
- [Adding Tools](../../developer-guide/adding-tools.md): authoring new Python tools and skills.
