---
name: create-new-tool
description: Create a new Agent Skill tool with template structure. This tool initializes a new skill directory under bash_tools/new_tools/ with SKILL.md template, scripts/, references/, and assets/ directories.
should_run_in_sandbox: main
returns_json: true

---

# Create New Tool

Scaffold a new bash_tools Skill under `bash_tools/new_tools/`.

## Usage

```bash
scripts/create_new_tool.sh my-tool-name --should_run_in_sandbox main --returns_json true
```

Optional:

```bash
scripts/create_new_tool.sh my-tool-name \
  --should_run_in_sandbox main \
  --returns_json true \
  --category retrieval \
  --requires_sandboxes "neo4j,joern"
```

## Parameters

- `tool_name` (**required**): hyphen-case, max 40 chars (e.g. `my-tool-name`)
- `--should_run_in_sandbox` (**required**): execution sandbox (e.g. `main`, `fuzz`, `neo4j`, `joern`)
- `--returns_json` (**required**): `true` or `false`
- `--category` (optional): subdir under `bash_tools/new_tools/` (default: `general`)
- `--requires_sandboxes` (optional): dependency sandboxes, comma-separated or `none`

## Return Value

```json
{{
  "success": true,
  "tool_name": "my-tool-name",
  "category": "general",
  "path": "/path/to/bash_tools/new_tools/general/my-tool-name",
  "message": "Tool created successfully"
}}
```

## Requires Sandbox

none

## Timeout

30 seconds
