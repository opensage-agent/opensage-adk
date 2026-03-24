# Adding a New Tool

## Overview

OpenSage supports three tool types:

1. **Python tools**: any callable function (docstring becomes the tool description)
2. **Agent Skills**: filesystem-discovered bash/Python scripts described by `SKILL.md`
3. **MCP toolsets**: external services exposed via MCP (typically SSE)

## Tool Types

### 1. Python Tools

Python tools are any callable functions you include in your agent’s `tools=[...]`.
The function signature becomes the tool schema, and the docstring is shown to
the model as the tool description.

**Example:**

```python
def greet(name: str) -> str:
    """Return a friendly greeting."""
    return f"Hello, {name}!"
```

**Enable in your agent:**

```python
root_agent = OpenSageAgent(
    name="my_agent",
    model=...,
    instruction="...",
    tools=[greet],
)
```

### 2. Agent Skills (Bash Scripts)

Agent Skills are bash scripts organized in a structured directory format with metadata. They are automatically discovered and loaded by the framework.

### 3. MCP Toolsets

MCP (Model Context Protocol) toolsets provide integration with external services or tools running in separate containers, typically accessed via SSE (Server-Sent Events) connections.

## Creating an Agent Skill

### Directory Structure

Create a skill directory under `src/opensage/bash_tools/`:

```
src/opensage/bash_tools/
└── category/
    └── tool-name/
        ├── SKILL.md          # Tool metadata and documentation
        └── scripts/
            └── tool_script.sh # The actual bash script
```

**Example structure:**
```
src/opensage/bash_tools/
└── retrieval/
    └── grep/
        ├── SKILL.md
        └── scripts/
            └── grep.sh
```

### SKILL.md Format

The `SKILL.md` file contains YAML frontmatter and markdown documentation:

````markdown
---
name: tool-name
description: Brief description of what the tool does
should_run_in_sandbox: main
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
````

Notes:

- `should_run_in_sandbox` is **required** for executable Skills (a Skill folder
  that contains `scripts/*.sh` or `scripts/*.py`).
- Use the Markdown section `## Requires Sandbox` for **dependency** sandboxes.
  (Do not put `sandbox` / `sandboxes` fields in YAML frontmatter.)

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
- `src/opensage/bash_tools/` (built-in tools)
- `~/.local/OpenSage/bash_tools/` (user-local skills/extensions)

The framework scans these directories for `SKILL.md` files and loads them automatically.

### enabled_skills (which skills get loaded)

Agents can restrict which skills are available by setting `enabled_skills`:

- `None`: load **no** skills
- `"all"` / `["all"]`: load **only top-level** skills (`<root>/*/SKILL.md`)
- `List[str]`: treat each entry as a **prefix allowlist** under the skill root
  (e.g. `"fuzz"` loads all skills under `fuzz/`; `"fuzz/run-fuzzing-campaign"` loads
  just that subtree)

### Per-skill dependency installers (deps/install.sh)

If a skill needs extra dependencies inside a sandbox, it can provide an installer:

- `deps/<sandbox_type>/install.sh` (sandbox-specific), and/or
- `deps/install.sh` (generic)

To control which sandbox should run the installer, add YAML frontmatter to
`SKILL.md`:

```yaml
---
should_run_in_sandbox: main
---
```

Installers are run during sandbox initialization (best-effort) and are only run
once per session (subsequent runs are skipped via a marker under `/shared`).

## Creating an MCP Toolset

MCP toolsets are created via Python functions that return `MCPToolset` instances:

```python
# src/opensage/toolbox/category/get_toolset.py
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseConnectionParams
from opensage.toolbox.decorators import requires_sandbox, safe_tool_execution
from opensage.utils.agent_utils import get_mcp_url_from_session_id

@safe_tool_execution
@requires_sandbox("gdb_mcp")
def get_toolset(session_id: str) -> MCPToolset:
    """Create MCPToolset with GDB MCP server running in Docker container.

    Args:
        session_id: Shared session ID for session-based management

    Returns:
        MCPToolset connected to GDB MCP server
    """
    url = get_mcp_url_from_session_id("gdb_mcp", session_id)
    mcp_toolset = MCPToolset(connection_params=SseConnectionParams(url=url))
    return mcp_toolset
```

The function should:
- Use `@safe_tool_execution` decorator
- Use `@requires_sandbox` to specify required sandbox types
- Return an `MCPToolset` instance
- Be registered in the agent's tools list

## Tool Registration

### For Python Tools

Add the callable directly to your agent’s `tools` list.

### For Agent Skills

Agent Skills are automatically discovered and registered. No manual registration needed.

### For MCP Toolsets

Add the toolset getter function to your agent's tools:

```python
from opensage.toolbox.category.get_toolset import get_toolset

agent = Agent(
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

[Tools](Getting-Started.md#tools) - Tool types and patterns
[Best Practices](Best-Practices.md) - Best practices for tools
