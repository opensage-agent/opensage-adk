# Project Structure

## Directory Overview

```
SAGE-X/
├── src/aigise/              # Main source code
│   ├── agents/              # Agent implementations
│   ├── cli/                 # Command-line interface
│   ├── config/              # Configuration system
│   ├── session/             # Session management
│   ├── sandbox/             # Sandbox implementations
│   ├── toolbox/             # Security analysis tools
│   ├── evaluations/         # Benchmark evaluations
│   ├── features/            # Framework features
│   ├── plugins/             # ADK plugins
│   ├── patches/             # ADK patches
│   └── templates/           # Configuration templates
├── examples/                # Example agents and usage
├── tests/                   # Test suite
├── docs/                    # Documentation
└── README.md               # Quick start guide
```

## Key Directories Explained

### `src/aigise/agents/`
- `aigise_agent.py`: Extended ADK agent class
- Tool loading and dynamic tool injection

### `src/aigise/session/`
- `aigise_session.py`: Main session manager
- `aigise_sandbox_manager.py`: Sandbox lifecycle management
- `aigise_dynamic_agent_manager.py`: Agent creation and caching
- `aigise_ensemble_manager.py`: Multi-agent coordination

### `src/aigise/sandbox/`
- `base_sandbox.py`: Abstract sandbox interface
- `native_docker_sandbox.py`: Docker-based sandbox
- `k8s_sandbox.py`: Kubernetes-based sandbox
- `initializers/`: Sandbox initialization logic

### `src/aigise/toolbox/`
- `static_analysis/`: Joern, CodeQL integration
- `fuzzing/`: Fuzzing tools
- `debugger/`: GDB integration
- `coverage/`: Coverage analysis
- `retrieval/`: Code search tools

### `src/aigise/evaluations/`
- `cybergym/`: CyberGym benchmark
- `patchagent/`: PatchAgent benchmark
- `secodeplt/`: SecCodePLT benchmark

## See Also

- [Core Components](Core-Components.md) - Component details
- [Development Guides](Development-Guides.md) - How to add to the codebase
