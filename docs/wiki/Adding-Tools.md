# Adding a New Tool

## Overview

In SAGE-X, tools are implemented as **Agent Skills** (bash scripts) or **MCP toolsets** (Model Context Protocol), rather than Python functions. This design allows tools to be executed directly in sandbox containers via bash commands, providing better isolation and flexibility.

## Tool Types

### 1. Agent Skills (Bash Scripts)

Agent Skills are bash scripts organized in a structured directory format with metadata. They are automatically discovered and loaded by the framework.

### 2. MCP Toolsets

MCP (Model Context Protocol) toolsets provide integration with external services or tools running in separate containers, typically accessed via SSE (Server-Sent Events) connections.

## Creating an Agent Skill

### Directory Structure

Create a skill directory under `src/<package>/bash_tools/`:

```
src/<package>/bash_tools/
└── category/
    └── tool-name/
        ├── SKILL.md          # Tool metadata and documentation
        └── scripts/
            └── tool_script.sh # The actual bash script
```

**Example structure:**
```
src/<package>/bash_tools/
└── retrieval/
    └── grep/
        ├── SKILL.md
        └── scripts/
            └── grep.sh
```

### SKILL.md Format

The `SKILL.md` file contains YAML frontmatter and markdown documentation:

```markdown
---
name: tool-name
description: Brief description of what the tool does
---

# Tool Name

Detailed description of the tool's functionality.

## Usage

```bash
scripts/tool_script.sh arg1 arg2 --option value
```

## Parameters

### param1 (required, positional position 0)

**Type**: `str`

Description of the parameter.

### param2 (optional, positional position 1)

**Type**: `int`

Description of the parameter.

### --option (optional, named parameter)

**Type**: `str`

Description of the option.

### --flag (optional, flag)

**Type**: `bool` (default: `false`)

Description of the flag.

## Return Value

Returns a JSON object with results:

```json
{
  "success": true,
  "result": "..."
}
```

## Requires Sandbox

main

## Timeout

Default timeout: 60 seconds
```

### Parameter Types

- **Positional parameters**: Specified with `positional position N` in the parameter description
- **Named parameters**: Use `--param_name value` format
- **Boolean flags**: Use `--flag` (no value needed)

### Bash Script Implementation

The bash script should:

1. Accept command-line arguments (positional and named)
2. Return JSON output for structured results
3. Use proper exit codes (0 for success, non-zero for errors)

**Example script:**

```bash
#!/bin/bash

# tool_script.sh - Tool description
# Usage: ./tool_script.sh param1 param2 --option value

if [ -z "$1" ]; then
    echo '{"error": "Missing required parameter"}'
    exit 1
fi

PARAM1="$1"
PARAM2="${2:-default}"

# Process --option if provided
OPTION=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --option)
            OPTION="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

# Tool logic here
RESULT=$(some_command "$PARAM1" "$PARAM2")

# Return JSON
echo "{\"success\": true, \"result\": \"$RESULT\"}"
```

### Sandbox Requirements

Specify required sandbox types in the `## Requires Sandbox` section:

```markdown
## Requires Sandbox

main
```

Or for multiple sandboxes:

```markdown
## Requires Sandbox

main, fuzz
```

### Automatic Discovery

Tools are automatically discovered from:
- `src/<package>/bash_tools/` (built-in tools)
- `~/.local/plugins/<product>/tools/` (user plugins)

The framework scans these directories for `SKILL.md` files and loads them automatically.

## Creating an MCP Toolset

MCP toolsets are created via Python functions that return `MCPToolset` instances:

```python
# src/<package>/toolbox/category/get_toolset.py
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseConnectionParams
from <package>.toolbox.decorators import requires_sandbox, safe_tool_execution
from <package>.utils.agent_utils import get_mcp_url_from_session_id

@safe_tool_execution
@requires_sandbox("gdb_mcp")
def get_toolset(aigise_session_id: str) -> MCPToolset:
    """Create MCPToolset with GDB MCP server running in Docker container.

    Args:
        aigise_session_id: Shared session ID for session-based management

    Returns:
        MCPToolset connected to GDB MCP server
    """
    url = get_mcp_url_from_session_id("gdb_mcp", aigise_session_id)
    mcp_toolset = MCPToolset(connection_params=SseConnectionParams(url=url))
    return mcp_toolset
```

The function should:
- Use `@safe_tool_execution` decorator
- Use `@requires_sandbox` to specify required sandbox types
- Return an `MCPToolset` instance
- Be registered in the agent's tools list

## Tool Registration

### For Agent Skills

Agent Skills are automatically discovered and registered. No manual registration needed.

### For MCP Toolsets

Add the toolset getter function to your agent's tools:

```python
from <package>.toolbox.category.get_toolset import get_toolset

agent = AigiseAgent(
    name="my_agent",
    tools=[get_toolset, ...],  # Add the getter function
    ...
)
```

## Best Practices

1. **Use bash scripts for container operations**: Prefer bash scripts for tools that interact with sandbox containers
2. **Return JSON**: Always return structured JSON from bash scripts for easy parsing
3. **Document thoroughly**: Include clear parameter descriptions and usage examples in `SKILL.md`
4. **Handle errors gracefully**: Use proper exit codes and error messages
5. **Specify sandbox requirements**: Always document which sandboxes are required
6. **Use MCP for external services**: Use MCP toolsets for tools that run in separate containers or services

## See Also

- [Common Patterns](Common-Patterns.md) - Tool development patterns
- [Best Practices](Best-Practices.md) - Best practices for tools
- [Core Concepts](Core-Concepts.md) - Understanding tools in context
