---
name: search-function
description: Tool to search for a function in the codebase.
should_run_in_sandbox: main
returns_json: false

---

# Search Function Tool

Tool to search for a function in the codebase.

## Usage

```bash
python3 /bash_tools/static_analysis/search-function/scripts/search_function.py "function_name"
```

## Parameters

### function_name (required, positional position 0)

**Type**: `str`

Function name to search for.

## Return Value

Returns plain text output listing found functions with their file paths, line numbers, and code snippets.

## Requires Sandbox

neo4j, codeql, joern
