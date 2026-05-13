# Patch Generation Scaffold

**Source:** [`agent_library/agents/patch_agent`](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents/patch_agent)

## What This Agent Does

A **minimal scaffold** you can copy when starting a new agent. Its "task" is to call `bash_tool_main` and echo `"Hello, world!"`; nothing more. There is no production workload here; the value is in the skeleton: a correct `mk_agent` signature, a real `OpenSageAgent` instantiation, and the minimum set of imports you need to grow from.

Use this when you want the shortest possible starting point that still resembles the other production agents in this directory: same `mk_agent(opensage_session_id: str)` entry, same `OpenSageAgent(...)` call, same single-tool wiring. From here you iterate by swapping the instruction, adding tools, and layering on sub-agents/ensembles as the task demands.

## Full Source

```python title="agent_library/agents/patch_agent/agent.py"
from google.adk.models.lite_llm import LiteLlm

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.general.bash_tool import bash_tool_main


def mk_agent(opensage_session_id: str):
    return OpenSageAgent(
        name="patch_generation_agent",
        model=LiteLlm(model="anthropic/claude-opus-4-7"),
        description="Generates Python patch scripts for vulnerabilities.",
        instruction="""
        You are a dummy agent. You should use bash_tool_main to echo "Hello, world!" and get the output.
        """,
        tools=[bash_tool_main],
    )
```

That's the whole file. The name hints at an eventual vulnerability-patching workload, but the shipped version is intentionally a stub.

## Run It

```bash
uv run opensage web \
  --agent agent_library/agents/patch_agent \
  --port 8000
```

No config file is shipped; the agent picks up the built-in default from `src/opensage/templates/configs/default_config.toml`.

## How to Turn It into Something Real

1. **Swap the instruction** with your task description and rules.
2. **Add tools.** Useful starting points:
    - `view_file`, `str_replace_edit`: file ops ([imports](../../reference/configuration/index.md)).
    - `run_terminal_command` + friends: bash access.
    - `finish_task`: explicit termination.
    - `create_subagent`, `call_subagent_as_tool`, `list_active_agents`: dynamic sub-agents.
    - `agent_ensemble`, `get_available_models`: fan-out to multiple models.
3. **Add a `config.toml`** if the default sandbox/LLM/history settings are not enough.
4. **Enable skills** (`enabled_skills=["retrieval", …]`) if you want the bundled bash-tool scripts.

See any of the other agents in this section for complete examples of each step.
