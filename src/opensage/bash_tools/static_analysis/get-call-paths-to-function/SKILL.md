---
name: get-call-paths
description: Get a path in the call graph from a source function to a specified destination function in the codebase. It's very useful when you want to find call paths to a function.
should_run_in_sandbox: main
returns_json: false

---

# Get Call Paths Tool

Get a path in the call graph from a source function to a specified destination function in the codebase.

## Usage

```bash
python3 /bash_tools/static_analysis/get-call-paths-to-function/scripts/get_call_paths_to_function.py "DST_FUNCTION" \
  --dst-file "path/to/dst_file" \
  --src-function "SRC_FUNCTION" \
  --src-file "path/to/src_file"
```

## Parameters

- `dst_function_name` (positional): Destination function name.
- `--dst-file`: File path where the destination function is defined (optional).
- `--src-function`: Source function name (optional; default `LLVMFuzzerTestOneInput`).
- `--src-file`: File path where the source function is defined (optional).

## Return Value

Returns JSON text with a top-level key `result` containing call path info.

## Requires Sandbox

neo4j, codeql, joern
