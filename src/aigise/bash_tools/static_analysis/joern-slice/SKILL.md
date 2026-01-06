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
scripts/joern_slice.sh "function_name" --file-path "relative/path/to/file.py"
```

## Parameters

- `function_name`: The name of the function to slice.
- `file_path`: (Optional) The file path where the function is defined.

## Return Value

Returns plain text output listing slice information for each file, including file paths and line numbers.

## Requires Sandbox

joern
