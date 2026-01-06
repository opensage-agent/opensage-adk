---
name: create-new-tool
description: Create a new Agent Skill tool with template structure. This tool initializes a new skill directory under bash_tools/new_tools/ with SKILL.md template, scripts/, references/, and assets/ directories. The new tool will be automatically discovered by the framework once created.
should_run_in_sandbox: main
returns_json: true

---

# Create New Tool

Initialize a new Agent Skill tool with a complete template structure, including SKILL.md documentation, scripts directory, and example files.

## Usage

```bash
scripts/create_new_tool.sh my-tool-name
```

With category:

```bash
scripts/create_new_tool.sh my-tool-name --category retrieval
```

## Parameters

### tool_name (required, positional position 0)

**Type**: `str`

Name of the new tool to create. Must be in hyphen-case (e.g., `my-new-tool`). The name will be used as the directory name and tool identifier.

**Requirements:**
- Lowercase letters, digits, and hyphens only
- Max 40 characters
- Must be a valid directory name

**Example**: `grep-tool`, `code-analyzer`, `file-processor`

### --category (optional, named parameter)

**Type**: `str` (default: `general`)

Category/subdirectory name under `bash_tools/new_tools/` where the tool will be created. If the category directory doesn't exist, it will be created automatically.

**Example**: `--category retrieval`, `--category fuzzing`, `--category static_analysis`

## Return Value

Returns a JSON object with creation result:

**Success case**:
```json
{{
  "success": true,
  "tool_name": "my-tool-name",
  "category": "general",
  "path": "/path/to/bash_tools/new_tools/general/my-tool-name",
  "message": "Tool created successfully"
}}
```

**Error case** (tool already exists):
```json
{{
  "success": false,
  "error": "Tool directory already exists: /path/to/bash_tools/new_tools/general/my-tool-name",
  "tool_name": "my-tool-name"
}}
```

**Error case** (invalid name):
```json
{{
  "success": false,
  "error": "Invalid tool name: 'My Tool'. Must be hyphen-case (e.g., 'my-tool')",
  "tool_name": "My Tool"
}}
```

## Behavior

1. **Validation**: Validates tool name format (hyphen-case, lowercase, max 40 chars)
2. **Path Construction**: Creates path as `bash_tools/new_tools/{category}/{tool_name}/`
3. **Directory Creation**: Creates the tool directory structure:
   - `SKILL.md` - Template with TODOs to complete
   - `scripts/` - Directory with example bash script
4. **Template Generation**: Populates all files with appropriate templates
5. **Next Steps**: Returns instructions for completing the tool

## Created Structure

```
bash_tools/new_tools/{category}/{tool_name}/
├── SKILL.md                    # Tool metadata and documentation template
└── scripts/
    └── {tool_name}.sh           # Example bash script (executable)
```

## Next Steps After Creation

1. Edit `SKILL.md` to complete the TODO items and update the description
2. Implement the bash script in `scripts/{tool_name}.sh`
3. If you need to add additional scripts for the tool, edit them directly in the `scripts/` directory under the tool's folder
4. Test the tool and ensure it returns proper JSON output
5. The tool will be automatically discovered by the framework on next agent initialization

## Requires Sandbox

main

## Timeout

30 seconds
