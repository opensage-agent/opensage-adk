# Introduction

## What is OpenSage?

OpenSage (Open Self-programming Agent Generation Engine) is an AI agent framework built on top of Google ADK (Agent Development Kit). It represents a paradigm shift from "Agent Development 1.0" to "Agent Development 2.0" — transitioning from agents that execute predefined structures to agents that can dynamically build and manage their own systems at runtime.

## Core Value Proposition

### From "Agent Using System" to "Agent Building System"

**Agent Development 1.0** (Traditional Frameworks):

- System structure pre-defined by humans (agent topology, workflows, tool sets)
- Agents can only choose actions within given structures
- Extending to new tasks requires significant manual refactoring
- Essentially human-centered agent building

**Agent Development 2.0** (OpenSage):

- Agents upgrade from "executing predefined structures" to "autonomously
  building and managing structures"
- Humans provide minimal, stable system-level scaffolding
- Everything else is generated, composed, and scheduled by AI at runtime
- Agents can "invent new capabilities" rather than just calling existing ones

## Key Features

- **AI-written tools** as first-class system entities
- **Runtime sub-agent creation** and dynamic agent topology
- **Memory as manageable resource** (graph-structured, Neo4j-backed)
- **Session-based resource management** with complete isolation
- **Multi-sandbox support** (Docker, Kubernetes under development)
- **Dynamic tool loading** and runtime tool generation
- **Agent ensemble** for parallel exploration
- **Integration with security tools** (Joern, CodeQL, GDB, Neo4j)
- **Evaluation framework** for security benchmarks

## Why OpenSage?

OpenSage was designed to make self-programming agent development feel more like software development. It provides:

- **Isolation**: Each agent session has its own resources, preventing
  interference
- **Flexibility**: Easy to add new tools, sandboxes, and agents
- **Evolution**: Tools and agents can be created and modified at runtime
- **Integration**: Seamless integration with existing security analysis tools
- **Composability**: Build complex agents from simple components
- **Scalability**: Kubernetes support (under development)

## Next Steps

[Getting Started](Getting-Started.md) - Set up your development environment
[Core Components](Core-Components.md) - Understand the core building blocks
[Entry Points](Entry-Points.md) - Understand how to use OpenSage
