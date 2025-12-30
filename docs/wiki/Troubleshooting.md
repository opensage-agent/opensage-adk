# Troubleshooting

## Common Issues

### Session not found

**Problem**: Session lookup fails

**Solutions**:
- Ensure session was created before use
- Check session ID matches exactly
- Verify session hasn't been cleaned up

### Sandbox not initialized

**Problem**: Sandbox access fails or sandbox is not ready

**Solutions**:
- Check sandbox initializer ran successfully
- Verify configuration has sandbox settings
- Check logs for initialization errors
- Wait for sandbox to be ready: `await session.sandboxes.wait_for_ready("main")`

### Tool not found

**Problem**: Agent cannot find or use a tool

**Solutions**:
- Verify tool is in agent's tools list
- Check tool decorators are correct
- Ensure tool is importable
- Check tool metadata if using dynamic loading

### Configuration not loading

**Problem**: Configuration errors or missing values

**Solutions**:
- Verify TOML file syntax
- Check template variable expansion
- Ensure config path is correct
- Validate required fields are present

## Debug Checklist

When debugging issues, check:

- [ ] Check session logs
- [ ] Verify sandbox container status
- [ ] Check configuration values
- [ ] Verify tool imports
- [ ] Check ADK compatibility
- [ ] Review error messages carefully

## Getting Help

- Check [Common Patterns](Common-Patterns.md) for examples
- Review [Best Practices](Best-Practices.md) for guidelines
- Check GitHub issues for known problems

## See Also

- [Testing Debugging](Testing-Debugging.md) - Debugging techniques
- [Development Guides](Development-Guides.md) - Development guides
