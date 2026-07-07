# PoC Generation Agent — Static Tools

**Source:** [`agent_library/agents/poc_agent_static_tools`](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents/poc_agent_static_tools)

## What This Agent Does

Given a vulnerability description and the source code of a vulnerable program, this agent generates a **proof-of-concept (PoC) input** that reproduces the crash. It is the OpenSage team's baseline PoC agent for the [CyberGym](https://github.com/cybergym) benchmark using a *static-only* tool palette.

The premise: most PoCs can be derived by reasoning carefully about the code path from the fuzzer harness (`LLVMFuzzerTestOneInput`) to the vulnerable function, without ever running the program locally. The agent submits candidate PoCs to the CyberGym server and iterates based on the oracle's response.

For the dynamic counterpart (that adds GDB, fuzzing, coverage) see [PoC Generation Agent — Dynamic Tools](./poc_agent_dynamic_tools.md).

## Key Design

- **Static-first philosophy.** No debugger, no fuzzer, no local execution. The prompt requires the agent to write an *ordered, end-to-end path* from entrypoint to crash before writing a PoC; if the path cannot be constructed, the candidate vulnerability is abandoned.
- **Skill-backed retrieval.** `enabled_skills=["new_tool_creator", "retrieval", "static_analysis", "neo4j"]` gives the agent access to bash-tool scripts (`read-file`, `list-functions-in-file`, `search-symbol-definition`, `get-call-paths-to-function`, `joern-query`, `neo4j-query`). The prompt explicitly prefers these over raw `grep`/`sed`.
- **Dynamic sub-agents mandatory.** The prompt promotes dynamic sub-agents to "preferred and default"; any subtask that can be broken down must go through `create_subagent` + `call_subagent`.
- **Submission via CyberGym oracle.** `generate_poc_and_submit` pushes a candidate PoC to the CyberGym server; `/shared/submit.sh` is the shell equivalent.

## Agent Source (Abbreviated)

```python title="agent_library/agents/poc_agent_static_tools/agent.py"
from opensage.agents.opensage_agent import OpenSageAgent
from benchmarks.cybergym.tools import (
    generate_poc_and_submit, run_poc_from_script,
)
from opensage.toolbox.finish_task.finish_task import finish_task
from opensage.toolbox.general.agent_tools import (
    critique, note_suspicious_things,
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
        model="openai/gpt-5.5",
        api_key=os.environ.get("LITELLM_API_KEY"),
        base_url=os.environ.get("LITELLM_BASE_URL"),
        reasoning_effort="medium",
    )
    return OpenSageAgent(
        name="poc_generation_agent",
        model=model,
        instruction=POC_STATIC_PROMPT,
        tools=[
            get_available_models,
            finish_task, generate_poc_and_submit,
            create_subagent, list_subagents, call_subagent,
            critique,
            list_background_tasks, get_background_task_output,
            run_terminal_command,
        ],
        enabled_skills=["new_tool_creator", "retrieval", "static_analysis", "neo4j"],
    )
```

See the [full prompt](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents/poc_agent_static_tools/agent.py) for the end-to-end reasoning template and the "Dynamic Agent Usage (Very Important)" rules.

## Run It

```bash
uv run opensage web \
  --agent agent_library/agents/poc_agent_static_tools \
  --config agent_library/agents/poc_agent_static_tools/config.toml \
  --port 8000
```

## Prerequisites

- CyberGym-compatible target layout: source at `/shared/code`, harness at `/src`, `/shared/submit.sh` present.
- Joern + Neo4j sidecars, declared in the config.
- `LITELLM_PROXY_API_KEY` if you keep the default `litellm_proxy/sage-gpt-5` model; otherwise swap `LiteLlm(model=…)` to your provider.
