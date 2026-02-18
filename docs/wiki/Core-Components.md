# Core Components

OpenSage core components.

## 1. Session

The central manager for all session-specific resources.

**Key Responsibilities:**

- Configuration management (TOML loading, env overrides)
- Agent lifecycle (creation, persistence, cleanup)
- Sandbox management (Docker containers, resource isolation)
- Neo4j client management
- Agent ensemble coordination

**Key Files:**

`opensage.session.session`, `opensage.session.dynamic_agent_manager`,
`opensage.session.sandbox_manager`

## 2. Agent

Extended ADK agent with security-focused features.

**Key Features:**

- Dynamic tool loading from filesystem
- Integration with sandbox environments
- Tool combo support
- Session-aware tool execution

**Key Files:**

Agent implementation lives under the `opensage.agents` namespace.

## 3. Sandbox System

Isolated execution environments for security analysis.

**Sandbox Types:**

- `main`: Primary analysis sandbox
- `joern`: Static analysis (CPG generation)
- `codeql`: CodeQL analysis
- `neo4j`: Graph database for CPG storage
- `gdb_mcp`: Debugger integration
- `fuzz`: Fuzzing environment

**Key Files:**

`opensage.sandbox.base_sandbox`, `opensage.sandbox.native_docker_sandbox`,
`opensage.sandbox.k8s_sandbox`, `opensage.sandbox.initializers`

**Docs:**

- [Sandbox System Guide](Sandboxes.md)
- [Adding a sandbox](Adding-a-Sandbox.md)
- [Adding a new sandbox backend](Adding-a-New-Sandbox-Backend.md)

## 4. Configuration System

TOML-based configuration with template variable expansion.

**Key Files:**

`opensage.config.config_dataclass`, `opensage.templates.configs.default_config`

**Docs:**

- [Configuration](Configuration.md)

## 5. Tools

Tools are not only security-specific. OpenSage tools typically come from:

- **Skills (`bash_tools/`)**: filesystem-discovered bash/Python scripts described
  by `SKILL.md` and executed in sandboxes.
- **Python tools (`toolbox/`)**: Python callables and wrappers (often orchestrate
  skills, sandboxes, storage, and MCP toolsets).
- **MCP toolsets**: external services exposed via MCP (commonly SSE).

**Tool Categories:**

- Skills: retrieval/search, editing utilities, submissions, etc.
- Static Analysis: Joern, CodeQL queries
- Dynamic Analysis: fuzzing, debugging
- Coverage: coverage collection and reporting
- Evaluation: benchmark execution and submission helpers

**Key Files:**

`src/opensage/bash_tools/`, `opensage.toolbox`

**Docs:**

- [Tools](Getting-Started.md#tools)
- [Adding a New Tool](Adding-Tools.md)

## Related Topics

- [Adding a Evaluation Benchmark](Adding-Evaluations.md)
- [Development Guides](Development-Guides.md) - How to extend components
