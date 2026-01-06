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

### Agent Skills (Bash Scripts)

- ✅ Create structured directory with `SKILL.md` and `scripts/` subdirectory
- ✅ Document all parameters in `SKILL.md` with types and descriptions
- ✅ Specify sandbox requirements in `## Requires Sandbox` section
- ✅ Return JSON output for structured results
- ✅ Use proper exit codes (0 for success, non-zero for errors)
- ✅ Handle errors gracefully with informative JSON error messages
- ✅ Use positional and named parameters appropriately
- ✅ Set appropriate timeout values

### MCP Toolsets

- ✅ Use `@safe_tool_execution` decorator
- ✅ Use `@requires_sandbox` to specify required sandbox types
- ✅ Return `MCPToolset` instances from getter functions
- ✅ Document connection parameters and usage

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
