# Issue-Fixing Agent (SWE-Bench Pro)

**Source:** [`agent_library/agents/swebenchpro_agent`](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents/swebenchpro_agent)

## What This Agent Does

`swebenchpro_agent` is the OpenSage team's submission agent for [SWE-Bench Pro](https://www.swebench.com/). It fixes a bug described in a natural-language issue by exploring a real repository, reproducing the failure, editing source files, running the project's test suite, and finally emitting a `prediction.patch` file ready for evaluation.

Two things make this agent different from a vanilla coding agent:

1. **Long-term graph memory.** The `search_memory` tool gives the agent access to a Neo4j-backed memory store populated by `MemoryObserverPlugin` across runs. Bug patterns and reproduction recipes seen before come back as retrieved context.
2. **Two-phase workflow (explore -> solve).** The file exports two agents: `mk_agent` (the solver) and `mk_explore_agent` (a read-only explorer). The explorer produces a structured "Exploration Summary" that the solver consumes as its starting context. The explorer never modifies code.

## Key Design

- **Explore-then-solve.** Running an explorer first is essentially a pre-computed reconnaissance pass. Knowledge it gathers is recorded automatically by `MemoryObserverPlugin` (no explicit store/call).
- **Structured handoff.** The explorer's final output follows a strict template (`### 1. Critical Files (MUST READ)`, `### 2. Bug Location Hypothesis`, `### 3. NOT FOUND`, and similar) that the solver's prompt references by section name.
- **Multi-file-change checklist.** The solver's prompt has an explicit "Before `finish_task`" checklist that enumerates ways multi-file refactors go wrong (missed callers, imports, helpers).
- **Tool output persistence.** Long tool outputs are written to `/workspace/.tool_outputs/` and `/workspace/.memory_observer_outputs/`; the agent knows to read those if a summarized tool response is not enough.
- **Multiple sandbox variants.** The directory ships `config.toml`, `docker_config.toml`, `agentdocker_lite_config.toml`, `agentdocker_lite_overlayfs_config.toml`, `docker_no_neo4j_config.toml`: different sandbox backends for different eval rigs.

## Solver Source (Abbreviated)

```python title="agent_library/agents/swebenchpro_agent/agent.py"
from opensage.agents.opensage_agent import OpenSageAgent
from opensage.memory.database_based.long_term.search_tool import search_memory
from opensage.toolbox.finish_task.finish_task import finish_task
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
from opensage.toolbox.general.fileop import str_replace_edit, view_file

def mk_agent(opensage_session_id, model=None, planner=None):
    if model is None:
        model = LiteLlm(
            model="litellm_proxy/openai/gpt-5.4",
            api_key=os.environ.get("LITELLM_API_KEY"),
            base_url=os.environ.get("LITELLM_BASE_URL"),
            cache_control_injection_points=[
                {"location": "message", "role": "system"},
                {"location": "message", "index": -2},
                {"location": "message", "index": -1},
            ],
        )

    return OpenSageAgent(
        name="benchmark_agent",
        model=model,
        planner=planner,
        instruction=SOLVER_PROMPT,   # see repo for the full prompt
        tools=[
            finish_task,
            view_file, str_replace_edit,
            agent_ensemble, agent_ensemble_pairwise,
            get_available_agents_for_ensemble, get_available_models,
            create_subagent, list_active_agents, call_subagent_as_tool,
            list_background_tasks, get_background_task_output,
            run_terminal_command, list_available_scripts,
            search_memory,
        ],
    )
```

`mk_explore_agent` is a read-only variant with a shorter tool list (`view_file`, terminal commands only; no edits). See the [source](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents/swebenchpro_agent/agent.py) for both prompts.

## Run It

For standard SWE-Bench Pro evaluation the repo is mounted at `/app` and the agent writes `prediction.patch` to `/shared`:

```bash
uv run opensage web \
  --agent agent_library/agents/swebenchpro_agent \
  --config agent_library/agents/swebenchpro_agent/config.toml \
  --port 8000
```

Use `docker_no_neo4j_config.toml` instead if you do not want the Neo4j memory backend; `search_memory` will degrade gracefully.

## Prerequisites

- Neo4j sandbox (auto-launched from `[sandbox.sandboxes.neo4j]` unless you use the `no_neo4j` config).
- A model that supports prompt caching if you want the 3-point `cache_control_injection_points` hint to reduce cost on long sessions.
