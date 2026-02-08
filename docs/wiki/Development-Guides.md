# Development Guides

This section covers how to extend SAGE-X with new functionality.

## Guides

- [Adding Tools](Adding-Tools.md) - How to add new tools
- [Sandboxes](Sandboxes.md) - Sandbox backends and how to add new sandbox types
- [Adding Evaluations](Adding-Evaluations.md) - How to add evaluation benchmarks

## Extending AigiseAgent

To add features to AigiseAgent:

1. Modify `src/aigise/agents/aigise_agent.py`
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

- [Best Practices](Best-Practices.md) - Best practices for development
- [Common Patterns](Common-Patterns.md) - Common code patterns
- [Testing Debugging](Testing-Debugging.md) - Testing and debugging guide
