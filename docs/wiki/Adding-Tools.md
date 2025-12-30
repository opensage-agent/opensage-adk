# Adding a New Tool

## Overview

Tools are Python functions that agents can call to interact with sandboxes and perform actions.

## Steps

1. Create tool function in appropriate toolbox module
2. Add `@requires_sandbox` decorator if needed
3. Register tool metadata if using dynamic loading
4. Add tests

## Example

```python
# src/aigise/toolbox/my_category/my_tool.py
from aigise.toolbox.decorators import requires_sandbox
from aigise.utils.agent_utils import get_sandbox_from_context

@requires_sandbox("main")
async def my_new_tool(param: str, tool_context: ToolContext) -> dict:
    sandbox = get_sandbox_from_context(tool_context, "main")
    result, exit_code = sandbox.run_command_in_container(f"echo {param}")
    return {"output": result, "exit_code": exit_code}
```

## Tool Decorators

- `@requires_sandbox("main")`: Specify required sandbox types
- Tool context is automatically injected

## Dynamic Tool Loading

For tools loaded from filesystem:
1. Create directory structure: `tools/my_tool/`
2. Add `SKILL.md` with metadata
3. Tools are automatically discovered

## See Also

- [Common Patterns](Common-Patterns.md) - Tool development patterns
- [Best Practices](Best-Practices.md) - Best practices for tools
- [Core Concepts](Core-Concepts.md) - Understanding tools in context
