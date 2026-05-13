# CTF Agent

**Source:** [`agent_library/agents/ctf_agent`](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents/ctf_agent)

## What This Agent Does

`ctf_agent` solves capture-the-flag challenges, typically reverse engineering and binary exploitation tasks where the agent is handed a binary and asked to extract a flag. It is the most **tool-heavy** agent in this collection: it wires four different binary-analysis/debugging MCP toolsets together (Ghidra, IDA Pro, pyghidra, GDB) so the model can pick whichever decompiler or debugger fits the challenge.

The design choice that makes this work is **MCP-in-subagent**: rather than calling MCP tools directly from the root agent (which would flood the root's context with tool schemas from four large MCP toolsets), the root agent spawns dedicated sub-agents and injects only the toolset(s) they need. The root orchestrates; the sub-agents execute.

## Key Design

- **Four binary-analysis MCP toolsets.** Ghidra, IDA Pro, pyghidra, and GDB: different tools work better on different binaries.
- **MCP-in-subagent pattern.** The system prompt explicitly tells the root: *"Perform MCP actions inside subagents rather than directly from the root agent."* This keeps the root's context clean.
- **Claude Opus with prompt caching.** The default model is Claude Opus accessed via a local LiteLLM proxy; the `cache_control_injection_points` hint is placed on the system message and the last two messages so repeated long-context calls hit cache.
- **Skills disabled.** `enabled_skills=[]`: tools come exclusively from the MCP services and the general orchestration set.

## Agent Source

```python title="agent_library/agents/ctf_agent/agent.py"
from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.binary.ghidra_mcp.get_toolset import get_toolset as get_ghidra_toolset
from opensage.toolbox.binary.ida_pro_mcp.get_toolset import get_toolset as get_ida_pro_toolset
from opensage.toolbox.binary.pyghidra_mcp.get_toolset import get_toolset as get_pyghidra_toolset
from opensage.toolbox.debugger.gdb_mcp.get_toolset import get_toolset as get_gdb_toolset
from opensage.toolbox.benchmark_specific.cybergym.cybergym import critique
from opensage.toolbox.general.agent_tools import (
    agent_ensemble, agent_ensemble_pairwise, complain,
    get_available_agents_for_ensemble, get_available_models,
)
from opensage.toolbox.general.bash_tools_interface import (
    get_background_task_output, list_available_scripts,
    list_background_tasks, run_terminal_command,
)
from opensage.toolbox.general.dynamic_subagent import (
    call_subagent_as_tool, create_subagent, list_active_agents,
)

def mk_agent(opensage_session_id):
    model = LiteLlm(
        model="claude-opus-4-7",
        base_url=os.environ.get("LITELLM_BASE_URL"),
        api_key=os.environ.get("LITELLM_API_KEY"),
        cache_control_injection_points=[
            {"location": "message", "role": "system"},
            {"location": "message", "index": -2},
            {"location": "message", "index": -1},
        ],
    )
    gdb_toolset      = get_gdb_toolset(opensage_session_id)
    ida_pro_toolset  = get_ida_pro_toolset(opensage_session_id)
    pyghidra_toolset = get_pyghidra_toolset(opensage_session_id)
    ghidra_toolset   = get_ghidra_toolset(opensage_session_id)

    return OpenSageAgent(
        name="ctf_agent",
        model=model,
        instruction="""
        You are a CTF agent that solves CTF challenges.
        For reverse engineering workflows, use `create_subagent` and inject the
        MCP toolsets you need by name from this agent's available Python toolsets
        (for example `ida_pro_mcp`, `pyghidra_mcp`, `ghidra_mcp`, `gdb_mcp`).
        Perform MCP actions inside those subagents rather than directly from the
        root agent.
        """,
        tools=[
            agent_ensemble, agent_ensemble_pairwise,
            get_available_agents_for_ensemble, get_available_models,
            create_subagent, list_active_agents, call_subagent_as_tool,
            critique, complain,
            list_background_tasks, get_background_task_output,
            run_terminal_command, list_available_scripts,
            gdb_toolset, ida_pro_toolset, pyghidra_toolset, ghidra_toolset,
        ],
        enabled_skills=[],
    )
```

## Run It

```bash
uv run opensage web \
  --agent agent_library/agents/ctf_agent \
  --config agent_library/agents/ctf_agent/config.toml \
  --port 8000
```

The example ships a `main_sandbox/` directory with the Dockerfile for the primary sandbox where the CTF binary runs.

## Prerequisites

- Ghidra, IDA Pro, pyghidra, and GDB MCP sandboxes declared in `config.toml`. Each is a separate container.
- IDA Pro requires a valid license; you can remove `ida_pro_toolset` from the tools list if unavailable.
- By default the model points at `http://localhost:8082` (a local LiteLLM / proxy endpoint). Swap the `LiteLlm(...)` arguments for whatever provider you use.
