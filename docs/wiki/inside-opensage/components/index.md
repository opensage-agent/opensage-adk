---
icon: lucide/toy-brick
---

# Components

OpenSage-ADK is a thin, opinionated layer on top of [Google ADK](https://adk.dev/) aimed at building long-horizon, tool-heavy agents: the kind that solve SWE-Bench Pro tasks, trigger CVEs, analyze binaries, or run for hundreds of tool calls without falling over.

The rest of this section walks through the concepts you'll meet once you move past the [Quick Start](../../get-started/quick-start.md) and start changing things. Each page traces a single subsystem back to the source under `src/opensage/`.

## The Building Blocks

An OpenSage agent is built from six composable subsystems:

- **[Sessions](./sessions.md)**: the per-run root object that owns configuration, sandbox handles, Neo4j clients, the model registry, and the agent manager. One session equals one `opensage_session_id` equals one isolated run.
- **[Sandboxes](./sandbox.md)**: isolated execution environments (containers or native) where every tool call runs. A single session can hold many sandboxes (`main`, `neo4j`, `gdb_mcp`, and similar).
- **[Tools](./tools.md)**: the verbs the model can use. Python functions, MCP toolsets, and bash skills normalize into a single tool list before the model sees them.
- **[Plugins](./plugins.md)**: small opt-in behaviors that hook into the agent lifecycle (after tool, before model, on event). Plugins layer in features such as history summarization, quota tracking, and doom-loop detection.
- **[History](./history.md)**: the strategy that keeps long runs from overflowing the context window, through two levers: truncate individual tool responses, and compact the running event log.
- **[Multi-Agent](./multi-agent.md)**: sub-agents declared at construction time or created at runtime, and per-model delegation through `call_subagent`.

## The `mk_agent` Factory

Every agent directory exposes a `mk_agent(opensage_session_id: str)` factory. It receives a session id and returns a root agent, usually an `OpenSageAgent`. Keep the factory deterministic and free of heavy import-time work.

```python title="my_agent/agent.py"
from opensage.agents.opensage_agent import OpenSageAgent

def mk_agent(opensage_session_id: str):
    return OpenSageAgent(
        name="my_agent",
        model=...,
        instruction="...",
        tools=[...],
    )
```

The `opensage_session_id` is a user-chosen string, often a UUID, that scopes every resource created for this run: sandboxes, Neo4j clients, agent instances, and peer-message inboxes. The framework calls `get_opensage_session(opensage_session_id)` to route those resources correctly.

`OpenSageAgent` subclasses ADK's `LlmAgent` (`src/opensage/agents/opensage_agent.py`). It adds tool normalization (Python functions and MCP toolsets wrap into a uniform shape), `enabled_skills` handling for the bash-skills system, and automatic prompt injection describing which skills the agent can invoke.

## Runtime Shape

```
┌────────────────── opensage  ─────────────────────┐
│                                                  │
│   OpenSageSession(session_id)                    │
│   ├── config (OpenSageConfig, from TOML)         │
│   ├── budget (BudgetManager)                     │
│   ├── sandboxes   (OpenSageSandboxManager)       │
│   ├── neo4j       (OpenSageNeo4jClientManager)   │
│   ├── ADK services (session/artifact/memory/auth)│
│   ├── llms        (LlmRegistry)                  │
│   └── agent_manager (AgentManager)               │
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
│   └── opensage/            # Core Python package (current layout)
│       ├── agents/          # Base agent + tool loading
│       ├── bash_tools/      # Agent Skills (SKILL.md + scripts/)
│       ├── cli/             # CLI entry points (opensage web / run / review / dependency-check)
│       ├── config/          # TOML config system + dataclasses
│       ├── evaluation/      # Evaluation runners, dispatchers, RL adapters
│       ├── features/        # Optional behaviors (summarization, session service)
│       ├── memory/          # File-based memory (short-term + long-term)
│       ├── orchestration/   # AgentManager, inboxes, sub-agent run loops
│       ├── plugins/         # ADK plugins + Claude Code hooks
│       ├── sandbox/         # Sandbox backends + initializers
│       ├── sandbox_scripts/ # Scripts invoked inside sandboxes
│       ├── session/         # OpenSageSession + sandbox/neo4j managers
│       ├── templates/       # Default configs + Dockerfiles
│       ├── toolbox/         # Python tool wrappers / MCP toolsets
│       └── utils/           # Shared utilities
├── agent_library/           # Example agents and configs
├── benchmarks/              # Benchmark runners (cybergym, swe_bench_pro, cybench, ...)
├── rl/                      # RL training integrations (areal, slime, nemo_rl)
├── tests/                   # Unit/integration tests
└── third_party/             # External benchmark/tool dependencies
```


## Where to Go Next

- If you are **building an agent**, the per-feature pages are the fastest path: [Sessions](./sessions.md) -> [Sandboxes](./sandbox.md) -> [Tools](./tools.md).
- If you are **tuning a long-running agent**, start with [History](./history.md) and [Plugins](./plugins.md).
- If you are **orchestrating several models**, read [Multi-Agent](./multi-agent.md).
- If you want to **extend the framework itself**, the [Developer Guide](../../developer-guide/adding-tools.md) is the companion reference.
