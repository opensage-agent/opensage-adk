# Architecture Overview

## Design Principles

1. **Session Isolation**: Each session has its own resources (sandboxes, configs, agents)
2. **No Global Singletons**: All resources are session-bound
3. **Composability**: Build complex agents from simple components
4. **Tool Integration**: Seamless integration with security analysis tools
5. **Extensibility**: Easy to add new tools, sandboxes, and agents

## Component Relationships

- **AigiseSession** is the central manager that coordinates all resources
- **AigiseAgent** extends ADK agents with self-programming capabilities
- **Sandboxes** provide isolated execution environments
- **Configuration** is TOML-based with template variables
- **Tools** are dynamically loaded and session-aware

## See Also

- [Core Components](Core-Components.md) - Detailed component descriptions
- [Core Concepts](Core-Concepts.md) - Core concepts explained
- [Project Structure](Project-Structure.md) - Code organization
