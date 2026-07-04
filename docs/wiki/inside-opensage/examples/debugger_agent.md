# Dynamic Analysis / Debugger Agent

**Source:** [`agent_library/agents/debugger_agent`](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents/debugger_agent)

## What This Agent Does

A focused sub-agent that steps through a **live, running program with GDB** to verify or refute a hypothesis about a crash. Its callers (typically [PoC Generation Agent — Dynamic Tools](./poc_agent_dynamic_tools.md) or similar orchestrators) invoke it with a concrete checklist:

> *"Here's the vulnerable program, here's the PoC file, here's the behavior I expect. Tell me whether it actually behaves that way."*

The agent uses the **GDB MCP toolset** to drive breakpoints, step execution, and inspect state. It is not meant to solve the whole bug-finding task on its own; it is a specialist invoked by a root agent when dynamic ground-truth is needed.

## Key Design

- **Small, specialist agent.** Built to be called by a parent agent as a tool. Returns focused answers, not plans.
- **GDB MCP integration.** `get_gdb_toolset(opensage_session_id)` is fetched once per session; the underlying MCP service runs in its own `[sandbox.sandboxes.gdb_mcp]` container.
- **Frugal-by-policy prompt.** The system prompt explicitly says "solve using as few tools as possible" and "stop if remaining LLM call budget is low (< 3)": a hard budget guard, because debugging loops can escalate quickly.
- **Concrete expectations required.** The prompt forbids exploratory debugging without a testable hypothesis ("what is the expected behavior, you should have concrete expectations to check").
- **Input discipline.** Only PoC files in `/shared` are valid inputs; the agent copies files there first if they live elsewhere.

## Agent Source

```python title="agent_library/agents/debugger_agent/agent.py"
from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.debugger.gdb_mcp.get_toolset import get_toolset as get_gdb_toolset
from opensage.toolbox.general.agent_tools import (
    complain, critique,
    note_suspicious_things, think,
)
from opensage.toolbox.general.bash_tools_interface import (
    get_background_task_output,
    list_background_tasks, run_terminal_command,
)
from opensage.toolbox.general.orchestration_tools import (
    call_subagent, create_subagent, get_available_models, list_subagents,
)

def mk_agent(opensage_session_id):
    model = LiteLlm(
        model="openai/gpt-5.4",
        api_key=os.environ.get("LITELLM_API_KEY"),
        base_url=os.environ.get("LITELLM_BASE_URL"),
        cache_control_injection_points=[
            {"location": "message", "role": "system"},
            {"location": "message", "index": -2},
            {"location": "message", "index": -1},
        ],
    )
    gdb_toolset = get_gdb_toolset(opensage_session_id)

    return OpenSageAgent(
        name="debugger_agent",
        model=model,
        description=(
            "A debugger agent that can debug the vulnerable program. "
            "When calling this tool, you should tell the debugger what is the "
            "vulnerable program and what is the poc, and what is the expected "
            "behavior, you should have concrete expectations to check."
        ),
        instruction="""
        You are a debugger agent that can debug the vulnerable program.
        ...
        You should solve the request using as least number of tools as possible.
        If you consistently encounter errors or your remaining LLM call budget
        is low (< 3), you should stop exploring further and immediately report
        your progress.
        """,
        tools=[
            complain,
            gdb_toolset,
            list_background_tasks, run_terminal_command,
            create_subagent, call_subagent, list_subagents,
            critique,
        ],
    )
```

See the [source](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents/debugger_agent/agent.py) for the full, detailed system prompt.

## Run It Standalone

```bash
uv run opensage web \
  --agent agent_library/agents/debugger_agent \
  --config agent_library/agents/debugger_agent/config.toml \
  --port 8000
```

## Run It as a Sub-Agent

The typical deployment is to have a parent agent (e.g. [PoC Generation Agent — Dynamic Tools](./poc_agent_dynamic_tools.md)) call this as a sub-agent via `call_subagent`. The parent provides the vulnerable program path, the PoC file, and the expected behavior; this agent returns its findings.

## Prerequisites

- GDB MCP sandbox (auto-launched from `[sandbox.sandboxes.gdb_mcp]`).
- PoC file placed at `/shared/<name>` before invocation.
- `LITELLM_PROXY_API_KEY` or a model swap in `agent.py`.
