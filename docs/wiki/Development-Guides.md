# Development Guides

This section covers how to extend OpenSage with new functionality.

## Guides

- [Adding Tools](Adding-Tools.md) - How to add new tools
- [Sandboxes](Sandboxes.md) - Sandbox backends and how to add new sandbox types
- [Adding a Evaluation Benchmark](Adding-Evaluations.md) - How to add evaluation benchmarks

## Extending the Base Agent

To add features to the base agent:

1. Modify code under `src/opensage/agents/`
2. Ensure backward compatibility
3. Add configuration options if needed
4. Update documentation

## General Development Workflow

1. Create feature branch
2. Make changes with tests
3. Run tests and linting
4. Update documentation
5. Submit PR

## See Also

[Best Practices](Best-Practices.md) - Best practices for development
[Tools](Getting-Started.md#tools) - Tool types and patterns
[Testing Debugging](Testing-Debugging.md) - Testing and debugging guide
