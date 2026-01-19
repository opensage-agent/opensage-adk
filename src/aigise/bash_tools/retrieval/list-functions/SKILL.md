---
name: list-functions
description: Tool to list all functions in a file using Neo4j.
should_run_in_sandbox: main
returns_json: false

---

# List Functions Tool

List functions in a file.

## Usage

```bash
python3 /bash_tools/retrieval/list-functions/scripts/list_functions.py --file "relative/path/to/file.py"
```

## Parameters

### --file (required, named parameter)

**Type**: `str`

Relative path to the file.

## Return Value

Returns text output listing functions with file path and line ranges.

## Requires Sandbox

neo4j, codeql, joern
