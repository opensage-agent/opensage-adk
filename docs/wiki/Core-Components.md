# Core Components

SAGE-X core components (Note: code uses `aigise` package name for compatibility).

## 1. AigiseSession

The central manager for all session-specific resources.

**Key Responsibilities:**
- Configuration management (TOML loading, env overrides)
- Agent lifecycle (creation, persistence, cleanup)
- Sandbox management (Docker containers, resource isolation)
- Neo4j client management
- Agent ensemble coordination

**Key Files:**
- `src/aigise/session/aigise_session.py`
- `src/aigise/session/aigise_dynamic_agent_manager.py`
- `src/aigise/session/aigise_sandbox_manager.py`

## 2. AigiseAgent

Extended ADK agent with security-focused features.

**Key Features:**
- Dynamic tool loading from filesystem
- Integration with sandbox environments
- Tool combo support
- Session-aware tool execution

**Key Files:**
- `src/aigise/agents/aigise_agent.py`

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
- `src/aigise/sandbox/base_sandbox.py`
- `src/aigise/sandbox/native_docker_sandbox.py`
- `src/aigise/sandbox/k8s_sandbox.py`
- `src/aigise/sandbox/initializers/`

## 4. Configuration System

TOML-based configuration with template variable expansion.

**Key Files:**
- `src/aigise/config/config_dataclass.py`
- `src/aigise/templates/configs/default_config.toml`

## 5. Toolbox

Collection of security analysis tools.

**Tool Categories:**
- Static Analysis: Joern, CodeQL queries
- Dynamic Analysis: Fuzzing, debugging
- Coverage: LLVM coverage tools
- Retrieval: Code search and symbol lookup
- Evaluation: PoC submission and validation

**Key Files:**
- `src/aigise/toolbox/`

## Related Topics

- [Core Concepts](Core-Concepts.md) - Understanding how components work together
- [Architecture](Architecture.md) - System architecture overview
- [Development Guides](Development-Guides.md) - How to extend components
