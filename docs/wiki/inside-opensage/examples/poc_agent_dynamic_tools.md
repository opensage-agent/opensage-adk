# PoC Generation Agent — Dynamic Tools

**Source:** [`examples/agents/poc_agent_dynamic_tools`](https://github.com/opensage-agent/opensage-adk/tree/main/examples/agents/poc_agent_dynamic_tools)

## What This Agent Does

The richer counterpart to [PoC Generation Agent — Static Tools](./poc_agent_static_tools.md). Same goal (generate a PoC input that triggers a disclosed vulnerability), but equipped with **dynamic analysis tools** for when pure static reasoning gets stuck:

- A **GDB MCP toolset** for inspecting live program state.
- A **`run_poc_from_script` tool** that executes candidate PoCs against the vulnerable binary locally (via `arvo`) before submitting to the CyberGym oracle.
- **Fuzzing** and **coverage** skills to explore paths that the model cannot plan ahead.

The prompt explicitly orders the tools: *static first, dynamic only when stuck*. The debugger is flagged as "expensive; use only when coverage is not enough." This keeps runs cheap and reserves the slow tools for truly-stuck situations.

## Key Design

- **Hybrid static + dynamic.** Static skills (`retrieval`, `static_analysis`, `neo4j`) plus dynamic skills (`coverage`, `fuzz`) and a live GDB MCP sidecar.
- **Local verification before submission.** `run_poc_from_script` drops the generated input at `/tmp/poc` and runs the binary locally; the agent only submits via `generate_poc_and_submit` once a local crash is reproduced.
- **Strict "path-before-PoC" discipline.** Same as the static variant: write the entry-to-crash call path first; abandon any candidate for which the path is not complete.
- **Thinking discipline.** The prompt demands `think` before any other tool call; cheap tokens spent planning beat expensive tokens spent flailing.
- **Escape hatch for history rot.** If stuck, the agent is told to spawn a **fresh sub-agent with no history**; the accumulated context might be misleading.

## Agent Source (Abbreviated)

```python title="examples/agents/poc_agent_dynamic_tools/agent.py"
from opensage.agents.opensage_agent import OpenSageAgent
from opensage.session import get_opensage_session
from opensage.toolbox.benchmark_specific.cybergym.cybergym import (
    critique, generate_poc_and_submit, run_poc_from_script,
)
from opensage.toolbox.debugger.gdb_mcp.get_toolset import get_toolset as get_gdb_toolset
from opensage.toolbox.finish_task.finish_task import finish_task
from opensage.toolbox.general.agent_tools import (
    agent_ensemble, agent_ensemble_pairwise, complain,
    get_available_agents_for_ensemble, get_available_models,
    note_suspicious_things, think,
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
        name="poc_generation_agent",
        model=model,
        instruction=POC_DYNAMIC_PROMPT,
        tools=[
            agent_ensemble, agent_ensemble_pairwise,
            get_available_agents_for_ensemble, get_available_models,
            create_subagent, list_active_agents, call_subagent_as_tool,
            finish_task, generate_poc_and_submit, run_poc_from_script,
            critique, complain,
            list_background_tasks, get_background_task_output,
            run_terminal_command, list_available_scripts,
            gdb_toolset,
        ],
        enabled_skills=[
            "coverage", "fuzz", "neo4j",
            "new_tool_creator", "retrieval", "static_analysis",
        ],
    )
```

See the [source](https://github.com/opensage-agent/opensage-adk/tree/main/examples/agents/poc_agent_dynamic_tools/agent.py) for the full prompt; it covers entrypoint reasoning (`LLVMFuzzerTestOneInput` discipline), background-task polling, when to escalate to the debugger, and the history-reset escape hatch.

## Run It

```bash
uv run opensage web \
  --agent examples/agents/poc_agent_dynamic_tools \
  --config examples/agents/poc_agent_dynamic_tools/config.toml \
  --port 8000
```

## Prerequisites

- CyberGym layout: source at `/shared/code`, harness at `/src`, `/shared/submit.sh`.
- Joern + Neo4j sidecars (for static skills).
- GDB MCP sandbox (auto-launched from `[sandbox.sandboxes.gdb_mcp]`).
- `LITELLM_PROXY_API_KEY` set; or swap the model in `agent.py` to your provider.

## When to Use Which PoC Agent

| Situation | Use |
|-----------|-----|
| You can reason from source alone, want a cheap baseline | [Static](./poc_agent_static_tools.md) |
| Vulnerabilities require runtime shape (heap layout, race, deep parser state) | **Dynamic** (this one) |
| Ablations that isolate the contribution of dynamic tools | Run both, compare |
