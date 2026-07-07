# DevOps Engineering Agent

**Source:** [`agent_library/agents/devops_gym_agent`](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents/devops_gym_agent)

## What This Agent Does

`devops_gym_agent` is a general-purpose software-engineering agent aimed at the kind of work a junior DevOps engineer does every day:

- **Build & configuration**: fix build failures, resolve dependency issues.
- **Monitoring**: diagnose runtime anomalies using standard system tools (`top`, `htop`, `ps`, `watch`, `curl`, `df`, `netstat`, `lsof`).
- **Issue resolving**: generate patches that fix GitHub-style issues.
- **Test generation**: write tests that reproduce the bug and validate the fix.

It sits one step up from [`harbor_agent`](./harbor_agent.md): the tool palette is the same terminal + file-ops set, but the agent now has access to **dynamic sub-agent orchestration primitives** (`create_subagent` / `call_subagent` / `list_subagents`) and a **`think`** tool for explicit planning. No debugger sidecars, no graph-database memory; the premise of DevOps-Gym is that the raw Linux terminal plus a planning loop is enough.

## Key Design

- **`think` as a first-class tool.** The workflow in the system prompt explicitly tells the agent to plan before acting.
- **Fresh-perspective sub-agent for deadlock.** When stuck after several attempts, the agent is instructed to spin up a fresh sub-agent (`create_subagent` + `call_subagent`), optionally on a different model, for an unbiased second pass.
- **Pattern-specific completion.** For monitoring tasks, the agent writes a canonical `/workspace/monitor_result.txt` with the anomaly pattern and the reason: a contract the evaluator reads.
- **No evaluation scripts.** The prompt explicitly forbids reading or executing grading scripts in the environment.

## Agent Source

```python title="agent_library/agents/devops_gym_agent/agent.py"
from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.finish_task.finish_task import finish_task
from opensage.toolbox.general.agent_tools import (
    complain, think,
)
from opensage.toolbox.general.bash_tools_interface import (
    get_background_task_output,
    list_background_tasks, run_terminal_command,
)
from opensage.toolbox.general.fileop import str_replace_edit, view_file
from opensage.toolbox.general.orchestration_tools import (
    call_subagent, create_subagent, get_available_models, list_subagents,
)

def mk_agent(opensage_session_id, model=None):
    if model is None:
        model = LiteLlm(model="openai/gpt-5.5")
    return OpenSageAgent(
        name="devops_gym_agent",
        model=model,
        instruction=_SYSTEM_PROMPT,
        tools=[
            finish_task,
            think, complain,
            view_file, str_replace_edit,
            run_terminal_command,
            list_background_tasks, get_background_task_output,
            get_available_models,
            create_subagent, list_subagents, call_subagent,
        ],
    )
```

See the [source](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents/devops_gym_agent/agent.py) for the full `_SYSTEM_PROMPT` with per-task guidance (build, monitoring, issue-resolving, test-generation).

## Run It

```bash
uv run opensage web \
  --agent agent_library/agents/devops_gym_agent \
  --config agent_library/agents/devops_gym_agent/config.toml \
  --port 8000
```

The example ships a `Dockerfile` in the agent directory used to build the target image for the sandbox.
