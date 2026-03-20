---
name: get-caller
description: Tool to get the caller of a function in the codebase. It can help you traverse the callgraph and give you indirect calls.
should_run_in_sandbox: main
returns_json: false

---

# Get Caller Tool

Tool to get the caller of a function in the codebase.

## Usage

```bash
python3 /bash_tools/static_analysis/get-caller/scripts/get_caller.py "function_name" --file-path "relative/path/to/file.py"
```

## Parameters

### function_name (required, positional position 0)

**Type**: `str`

Function name to search for.

### --file-path (optional, named parameter)

**Type**: `str`

Optional file path where the function is defined.

## Return Value

Returns plain text output listing callers with their function names, file paths, line numbers, and call types.

## Requires Sandbox

neo4j, codeql, joern
