---
icon: lucide/folder-tree
---

# Project Structure

A map of the OpenSage-ADK source tree, with one-line notes on what each directory holds and where to look when extending the system.

## Directory Overview

```
OpenSage/
├── README.md
├── docs/                    # Docs source (Zensical)
├── src/
│   └── opensage/            # Core Python package
│       ├── agents/          # Base agent + tool loading
│       ├── bash_tools/      # Agent Skills (SKILL.md + scripts/)
│       ├── cli/             # CLI entry points (opensage web / dependency-check)
│       ├── config/          # TOML config system + dataclasses
│       ├── evaluation/      # Evaluation base classes, dispatchers, and RL adapters
│       ├── features/        # Feature flags / optional behaviors
│       ├── llm/             # LLM client abstractions
│       ├── memory/          # File-based memory storage and tools
│       ├── orchestration/   # Event persistence and orchestration helpers
│       ├── patches/         # Runtime compatibility patches
│       ├── plugins/         # ADK plugins
│       ├── sandbox/         # Sandbox backends + initializers
│       ├── sandbox_scripts/ # Scripts invoked inside sandboxes
│       ├── session/         # Session + managers (sandboxes/agents/neo4j/ensemble)
│       ├── templates/       # Default configs + Dockerfiles
│       ├── toolbox/         # Python tool wrappers / MCP toolsets
│       ├── workflow/        # Workflow helpers and supporting utilities
│       └── utils/           # Shared utilities
├── agent_library/           # Example agents and configs
├── benchmarks/              # Benchmark integrations and task runners
├── tests/                   # Unit and integration tests
└── third_party/             # External benchmark and tool dependencies
```

## Key Directories Explained

### `src/opensage/agents/`

OpenSage base agent and tool description loading (`ToolLoader`).

### `src/opensage/session/`

Session manager and per-session resource isolation. Owns sandbox lifecycle, dynamic agent management, and ensemble management.

### `src/opensage/sandbox/`

Abstract sandbox interface plus concrete backends:

- Docker-based sandbox.
- Kubernetes-based sandbox (under development).
- `initializers/`: per-sandbox initialization logic that runs after a container starts.

### `src/opensage/bash_tools/`

Filesystem-discovered Skills (bash tools). Each Skill is a directory containing `SKILL.md`, a `scripts/` subdirectory, and an optional `deps/` directory.

### `src/opensage/toolbox/`

Python-side tools, MCP toolsets, and the wrappers used by agents.

### `src/opensage/evaluation/`

Core evaluation abstractions, dispatchers, and RL adapter integrations. Benchmark-specific integrations live under the top-level `benchmarks/` directory.

### `benchmarks/`

Task runners and metadata for supported benchmarks, including:

- `agentbeats/`
- `cvegym/`
- `cybench/`
- `cybergym/`: CyberGym benchmark.
- `devopsgym/`
- `harbor/`
- `nyuctf/`
- `sage-ccb/`
- `secodeplt/`: SecCodePLT benchmark.
- `swe_bench_pro/`: SWE-Bench Pro benchmark.
- `termigen/`

## See Also

- [Components](components/index.md): runtime architecture for sessions, sandboxes, tools, plugins, and history.
- [Adding Tools](../developer-guide/adding-tools.md): how to add Python tools, Agent Skills, or MCP toolsets to an agent.
