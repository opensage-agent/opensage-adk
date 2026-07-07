---
icon: lucide/flask-conical
---

# Production Agents

The OpenSage team builds agents that target hard, real-world workloads: fixing bugs across a million lines of code, finding and triggering memory-safety vulnerabilities, solving CTF challenges, diagnosing runtime anomalies. All of them live under [`agent_library/agents/`](https://github.com/opensage-agent/opensage-adk/tree/main/agent_library/agents) in the repo, and every one ships with its `agent.py`, a matching `config.toml` (or several), and reproducible prompts.

This section walks through those production agents one by one. Unlike the [small examples in *Get Started*](../../get-started/examples/index.md), which each demonstrate a single OpenSage feature, these agents **combine many features** into complete solutions. They are the clearest guide to how `OpenSageAgent`, dynamic sub-agents, ensembles, long-term memory, MCP toolsets, and bash-skill sandboxes compose into something that actually solves a benchmark.

## Why Publish These Agents?

- **Reproducible baselines.** Each agent is the exact setup we use internally for SWE-Bench Pro, CyberGym, HarborBench, CTF evaluations, etc. If you want to compare against us, start from these.
- **Design patterns.** The agents illustrate several recurring tradeoffs: static vs dynamic toolsets, one-shot vs explore-then-solve, MCP-heavy vs bash-heavy, graph memory vs in-session memory.
- **Tool integration examples.** Each agent imports real toolsets from `opensage.toolbox.*` (GDB, Joern/Neo4j CPG, fuzzing, coverage, Ghidra/IDA Pro MCP, and similar). The `tools=[...]` list on each agent is the best reference for what plays well together.

## The Agents

### Software Engineering

| Agent | What it does |
|-------|--------------|
| [Terminal Coding Agent (Harbor)](./harbor_agent.md) | Minimal Linux-terminal coding baseline. Used as the entry point for T-Bench / Harbor evaluations. |
| [DevOps Engineering Agent](./devops_gym_agent.md) | Build fixes, runtime-anomaly monitoring, issue resolution, test generation. Purely bash + file-op tools plus multi-agent orchestration. |
| [Issue-Fixing Agent (SWE-Bench Pro)](./swebenchpro_agent.md) | Full SWE-Bench Pro fixer with long-term graph memory and a separate *explorer* agent that pre-scans the repo. |

### Vulnerability Research

| Agent | What it does |
|-------|--------------|
| [Static Vulnerability Detection Agent](./vul_agent_static_tools.md) | Reads a target function and its call-graph context from Joern/Neo4j; decides whether a vulnerability exists. |
| [PoC Generation Agent — Static Tools](./poc_agent_static_tools.md) | Generates a PoC input for a disclosed vulnerability using only static retrieval + bash skills. |
| [PoC Generation Agent — Dynamic Tools](./poc_agent_dynamic_tools.md) | Same goal, but adds the GDB MCP toolset, the fuzzing skill, and the coverage skill for when static reasoning is not enough. |
| [Dynamic Analysis / Debugger Agent](./debugger_agent.md) | A focused sub-agent that steps through a running program with GDB to verify hypotheses. Called by other agents as a tool. |
| [Patch Generation Scaffold](./patch_agent.md) | A minimal "hello-world" scaffold for a patch agent; copy-paste this when starting a new agent. |

## Shared Ingredients

Read the agent configs and you'll notice the same building blocks in almost every file:

- **A reasoning model**, often behind LiteLLM's proxy so it can be swapped without code changes.
- **`finish_task`** as the explicit termination tool: no "guess when to stop."
- **Dynamic sub-agents** (`create_subagent`, `call_subagent`, `list_subagents`, `get_available_models`) so the root agent can spawn specialists for each subtask, optionally on a different model.
- **Bash tools** (`run_terminal_command`, `list_background_tasks`, `get_background_task_output`) as the universal escape hatch.
- **`complain`** / **`think`** / **`critique`** as lightweight self-reflection levers.

Each agent then layers domain-specific tools on top: Joern/Neo4j for static analysis, GDB for dynamic analysis, fuzzing + coverage for triggering bugs.

## How to Adapt an Agent

Every page below has a **Run it** section with the exact `uv run opensage web …` command. Beyond that:

1. **Swap or configure the model.** Some agents use `litellm_proxy/...` model aliases. Set `LITELLM_PROXY_API_KEY` and `LITELLM_PROXY_BASE_URL`, or edit `agent.py` to point `LiteLlm(model=..., api_key=..., base_url=...)` at your provider.
2. **Swap the sandbox image.** The `config.toml` files declare target-specific Dockerfiles (`arvo:*` for CyberGym, `${TASK_NAME}_main` for T-Bench, etc.). Build your own and change `[sandbox.sandboxes.main.image]`.
3. **Trim the toolset.** The `tools=[...]` list is the starting point; drop anything you do not need.
4. **Tune the prompt.** The instruction strings are long and domain-specific; they are where most of the behavior lives.
