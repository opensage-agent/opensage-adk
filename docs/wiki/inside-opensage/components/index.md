---
icon: lucide/toy-brick
---

# Components

OpenSage-ADK is a thin, opinionated layer on top of [Google ADK](https://adk.dev/) aimed at building long-horizon, tool-heavy agents: the kind that solve SWE-Bench Pro tasks, trigger CVEs, analyze binaries, or run for hundreds of tool calls without falling over.

The rest of this section walks through the concepts you'll meet once you move past the [Quick Start](../../get-started/quick-start.md) and start changing things. Each page traces a single subsystem back to the source under `src/opensage/`.

## The Building Blocks

An OpenSage agent is built from six composable subsystems:

- **[Sessions](./sessions.md)**: the per-run root object that owns configuration, sandbox handles, Neo4j clients, ensembles, and dynamic agents. One session = one `session_id` = one isolated run.
- **[Sandboxes](./sandbox.md)**: isolated execution environments (containers or native) where every tool call actually runs. A single session can juggle many sandboxes (`main`, `neo4j`, `gdb_mcp`, and similar).
- **[Tools](./tools.md)**: the verbs the LLM can use. Python functions, MCP toolsets, bash skills, `ToolCombo` sequences: all normalized into a single tool list before the LLM sees them.
- **[Plugins](./plugins.md)**: small opt-in behaviors that hook into the agent lifecycle (after tool, before model, on event). Plugins are how features like history summarization, quota tracking, and doom-loop detection are layered in.
- **[History](./history.md)**: the strategy that keeps long runs from overflowing the context window. Two levers: truncate individual tool responses, and compact the running event log.
- **[Multi-Agent](./multi-agent.md)**: sub-agents as tools, dynamically-spawned sub-agents, and model ensembles that fan a sub-task across several models in parallel.

## The `mk_agent` Factory

Every agent directory exposes a `mk_agent(session_id: str)` factory. It receives a **session id** and returns a root agent (typically an `OpenSageAgent`). The factory should be deterministic and avoid heavy import-time work.

```python title="my_agent/agent.py"
from opensage.agents.opensage_agent import OpenSageAgent

def mk_agent(session_id: str):
    return OpenSageAgent(
        name="my_agent",
        model=...,
        instruction="...",
        tools=[...],
    )
```

The `session_id` is a user-chosen string (a UUID is a common choice) that scopes every resource created for this run: sandboxes, Neo4j clients, dynamic agents, ensemble message boards. The framework looks up `get_opensage_session(session_id)` to route those resources correctly.

`OpenSageAgent` itself is a subclass of ADK's `LlmAgent` (`src/opensage/agents/opensage_agent.py`). It adds: tool normalization (Python functions, MCP toolsets, `ToolCombo` sequences all get wrapped into a uniform shape), `enabled_skills` handling (the bash-skills system), and automatic prompt injection describing which skills the agent can invoke.

## Runtime Shape

```
┌────────────────── opensage  ─────────────────────┐
│                                                  │
│   OpenSageSession(session_id)                    │
│   ├── config (OpenSageConfig, from TOML)         │
│   ├── sandboxes   (OpenSageSandboxManager)       │
│   ├── neo4j       (OpenSageNeo4jClientManager)   │
│   ├── agents      (DynamicAgentManager)          │
│   ├── ensemble    (OpenSageEnsembleManager)      │
│   └── message_boards                             │
│                                                  │
│   root_agent = mk_agent(session_id) <─ your code │
│                                                  │
│   plugins = load_plugins(...)                    │
│                                                  │
│   ADK Runner.run(root_agent, session_service)    │
│     ↓                                            │
│   (LLM reason -> tool call -> sandbox exec -> …) │
│                                                  │
└──────────────────────────────────────────────────┘
```

The session is created first, sandboxes are launched, plugins are discovered, and then ADK's runner drives the reason-act loop. On shutdown, `cleanup_all_sessions()` stops containers and clears the registry.


## Directory Overview

```
opensage-adk/
├── README.md
├── docs/                    # Docs source (Zensical)
├── src/
│   └── opensage/              # Core Python package (current layout)
│       ├── agents/          # Base agent + tool loading
│       ├── bash_tools/      # Agent Skills (SKILL.md + scripts/)
│       ├── cli/             # CLI entry points (opensage web / dependency-check)
│       ├── config/          # TOML config system + dataclasses
│       ├── evaluations/     # Benchmarks + evaluation runners
│       ├── features/        # Feature flags / optional behaviors
│       ├── memory/          # Neo4j-backed memory (search/update/tools)
│       ├── plugins/         # ADK plugins
│       ├── sandbox/         # Sandbox backends + initializers
│       ├── sandbox_scripts/ # Scripts invoked inside sandboxes
│       ├── session/         # Session + managers (sandboxes/agents/neo4j/ensemble)
│       ├── templates/       # Default configs + Dockerfiles
│       ├── toolbox/         # Python tool wrappers / MCP toolsets
│       ├── util_agents/     # Utility sub-agents (e.g. memory management)
│       └── utils/           # Shared utilities
├── examples/                # Example agents and configs
├── tests/                   # Unit/integration tests
└── third_party/             # External benchmark/tool dependencies
```


## Where to Go Next

- If you are **building an agent**, the per-feature pages are the fastest path: [Sessions](./sessions.md) -> [Sandboxes](./sandbox.md) -> [Tools](./tools.md).
- If you are **tuning a long-running agent**, start with [History](./history.md) and [Plugins](./plugins.md).
- If you are **orchestrating several models**, read [Multi-Agent](./multi-agent.md).
- If you want to **extend the framework itself**, the [Developer Guide](../../developer-guide/adding-tools.md) is the companion reference.
