---
name: get-callee
description: Tool to get the callee of a function in the codebase by function name and file path.
should_run_in_sandbox: main
returns_json: false

---

# Get Callee Tool

Tool to get the callee of a function in the codebase by function name and file path.

## Usage

```bash
python3 scripts/get_callee.py "function_name" --file-path "relative/path/to/file.py"
```

## Parameters

### function_name (required, positional position 0)

**Type**: `str`

Function name to search for.

### --file-path (optional, named parameter)

**Type**: `str`

Optional file path where the function is defined.

## Return Value

Returns plain text output listing callees with their function names, file paths, line numbers, and call types.

## Requires Sandbox

neo4j, codeql, joern
