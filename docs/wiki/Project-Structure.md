# Project Structure

## Directory Overview

```
OpenSage/
├── README.md
├── docs/                    # Docs source (MkDocs)
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

## Key Directories Explained

### `src/opensage/agents/`
- OpenSage base agent and tool description loading (`ToolLoader`)

### `src/opensage/session/`
- Session manager and per-session resource isolation
- Sandbox lifecycle management
- Dynamic agent management and ensemble management

### `src/opensage/sandbox/`
- Abstract sandbox interface
- Docker-based sandbox
- Kubernetes-based sandbox (under development)
- `initializers/`: Sandbox initialization logic

### `src/opensage/bash_tools/`
- Filesystem-discovered Skills (bash tools)
- Each Skill: `SKILL.md` + `scripts/` (+ optional `deps/`)

### `src/opensage/toolbox/`
- Python-side tools, MCP toolsets, and wrappers used by agents

### `src/opensage/evaluations/`
- `cybergym/`: CyberGym benchmark
- `patchagent/`: PatchAgent benchmark
- `secodeplt/`: SecCodePLT benchmark
- `swe_bench_pro/` SWE-Bench Pro benchmark

## See Also

[Core Components](Core-Components.md) - Component details
[Development Guides](Development-Guides.md) - How to add to the codebase
