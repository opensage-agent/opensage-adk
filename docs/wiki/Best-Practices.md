# Best Practices

## Session Management

- ✅ Always use `get_aigise_session()` instead of creating sessions directly
- ✅ Clean up sessions when done: `cleanup_aigise_session(session_id)`
- ✅ Use unique session IDs for different runs

## Agent Development

- ✅ Keep agents focused on single responsibilities
- ✅ Use sub-agents for complex workflows
- ✅ Leverage tool combos for related tool groups
- ✅ Document tool parameters and return values

## Tool Development

- ✅ Use `@requires_sandbox` decorator for sandbox-dependent tools
- ✅ Extract session from context using utility functions
- ✅ Handle errors gracefully with informative messages
- ✅ Use async/await for I/O operations

## Configuration

- ✅ Use template variables for environment-specific values
- ✅ Document configuration options
- ✅ Validate configuration in initialization
- ✅ Provide sensible defaults

## Code Organization

- ✅ Follow existing module structure
- ✅ Use relative imports in source code
- ✅ Use absolute imports in tests
- ✅ Add docstrings to public APIs

## See Also

- [Common Patterns](Common-Patterns.md) - Common code patterns
- [Contributing](Contributing.md) - Contribution guidelines
