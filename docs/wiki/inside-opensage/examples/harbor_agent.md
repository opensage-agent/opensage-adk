# Terminal Coding Agent (Harbor)

**Source:** [`agent_library/agents/harbor_agent`](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents/harbor_agent)

## What This Agent Does

`harbor_agent` is the minimal **general-purpose terminal coding agent** that the OpenSage team uses as the baseline for [Harbor / T-Bench](https://github.com/laude-institute/t-bench) runs. It operates entirely inside a sandboxed Linux container with a small, disciplined toolset: terminal commands, file view/edit, and nothing else. No sub-agents, no ensembles, no MCP services. This is the "plain vanilla" production agent, good to copy when you want to start from a clean slate.

The prompt leans heavily on two principles: *verify your work by running the code* and *re-read the task before finishing*. Together, they are what keep a single-agent, single-model setup honest on coding tasks.

## Key Design

- **No multi-agent orchestration.** One root agent, one model, a short tool list.
- **File ops as first-class tools.** `view_file` and `str_replace_edit` are given directly to the LLM instead of asking it to assemble `sed` commands.
- **Background-task awareness.** The agent can start long-running commands, check their status, and fetch output later without blocking its own turn.
- **Skills disabled.** `enabled_skills=None`: no bash-tool scripts; the agent works from raw terminal primitives only.

## Agent Source

```python title="agent_library/agents/harbor_agent/agent.py"
from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.finish_task.finish_task import finish_task
from opensage.toolbox.general.bash_tools_interface import (
    get_background_task_output,
    list_background_tasks,
    run_terminal_command,
)
from opensage.toolbox.general.fileop import str_replace_edit, view_file

def mk_agent(opensage_session_id, model=None):
    if model is None:
        model = LiteLlm(model="openai/gpt-4o")

    return OpenSageAgent(
        name="harbor_agent",
        model=model,
        instruction=SYSTEM_PROMPT,
        tools=[
            finish_task,
            view_file, str_replace_edit,
            run_terminal_command,
            list_background_tasks, get_background_task_output,
        ],
        enabled_skills=None,
    )
```

The full `SYSTEM_PROMPT` (Role / Environment / Verify / Review / Best Practices) lives in the [source file](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents/harbor_agent/agent.py).

## Run It

```bash
uv run opensage web \
  --agent agent_library/agents/harbor_agent \
  --config agent_library/agents/harbor_agent/config.toml \
  --port 8000
```

The bundled `config.toml` uses `openai/gpt-4o` with history compaction at 240k characters. For T-Bench runs, the main sandbox image name is derived from `${TASK_NAME}`; override it per task.
