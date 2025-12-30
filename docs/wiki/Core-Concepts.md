# Core Concepts

## Session Management

Every operation in SAGE-X is scoped to a session. Sessions provide:
- Resource isolation
- Configuration management
- Lifecycle management

```python
from aigise import get_aigise_session

session = get_aigise_session("my_session_id", config_path="config.toml")
```

## Agent Creation

Agents are created through `mk_agent` functions:

```python
def mk_agent(aigise_session_id: str) -> AigiseAgent:
    session = get_aigise_session(aigise_session_id)
    # ... configure agent ...
    return AigiseAgent(...)
```

## Sandbox Lifecycle

Sandboxes are managed through the session:

```python
session = get_aigise_session("my_session_id")
sandbox = session.sandboxes.get_sandbox("main")
result = sandbox.run_command_in_container("ls /shared")
```

## Tool Development

Tools are Python functions with decorators:

```python
@requires_sandbox("main")
async def my_tool(param: str, tool_context: ToolContext) -> dict:
    sandbox = get_sandbox_from_context(tool_context, "main")
    # ... tool implementation ...
    return {"result": "..."}
```

## Configuration

Configuration is TOML-based with template variables:

```toml
src_dir_in_sandbox = "/shared/code"
[sandbox.sandboxes.main]
image = "ubuntu:20.04"
```

## See Also

- [Development Guides](Development-Guides.md) - Practical development examples
- [Common Patterns](Common-Patterns.md) - Common code patterns
- [Best Practices](Best-Practices.md) - Best practices
