---
name: create-new-tool
description: Scaffold a new bash_tools Skill under bash_tools/new_tools/.
should_run_in_sandbox: main
returns_json: true

---

# Create New Tool

Scaffold a new bash_tools Skill.

## Usage

```bash
/bash_tools/new_tool_creator/scripts/create_new_tool.sh my-tool-name --should_run_in_sandbox main --returns_json true
```

## Parameters

### tool_name (required, positional position 0)

**Type**: `str`

Tool name (hyphen-case, e.g. `my-tool-name`).

### --should_run_in_sandbox (required, named parameter)

**Type**: `str`

Execution sandbox (e.g. `main`, `fuzz`, `neo4j`, `joern`).

### --returns_json (required, named parameter)

**Type**: `str`

`true` or `false`.

### --category (optional, named parameter)

**Type**: `str`

Subdirectory under `bash_tools/new_tools/` (default: `general`).

### --requires_sandboxes (optional, named parameter)

**Type**: `str`

Dependency sandboxes (comma-separated) or `none`.

## Return Value

```json
{
  "success": true,
  "tool_name": "my-tool-name",
  "category": "general",
  "path": "/path/to/bash_tools/new_tools/general/my-tool-name",
  "message": "Tool created successfully"
}
```

## Requires Sandbox

none

## Timeout

30 seconds
