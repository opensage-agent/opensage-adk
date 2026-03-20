---
name: joern-slice
description: Tool to get the program slice for a given function using Joern.
should_run_in_sandbox: joern
returns_json: false

---

# Joern Slice Tool

Tool to get the program slice for a given function using Joern.

## Usage

```bash
/bash_tools/static_analysis/joern-slice/scripts/joern_slice.sh "function_name" --file-path "relative/path/to/file.py"
```

## Parameters

### function_name (required, positional position 0)

**Type**: `str`

Function name to slice.

### --file-path (optional, named parameter)

**Type**: `str`

Optional file path where the function is defined.

## Return Value

Returns plain text output listing slice information for each file, including file paths and line numbers.

## Requires Sandbox

joern
