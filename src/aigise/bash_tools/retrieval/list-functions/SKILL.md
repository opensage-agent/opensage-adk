---
name: list-functions
description: Tool to list all functions in a file using Neo4j.
should_run_in_sandbox: main
returns_json: false

---

# List Functions Tool

Tool to list all functions in a file using Neo4j to query the code property graph.

## Usage

```bash
python3 scripts/list_functions.py --file "relative/path/to/file.py" --neo4j-host "IP" --neo4j-port 7687
```

## Parameters

- `file`: The relative path to the file.
- `neo4j-host`: IP address of Neo4j container.
- `neo4j-port`: Bolt port of Neo4j container.

## Return Value

Returns text output listing all functions found in the file, with each function showing:
- Function name
- File path
- Start and end line numbers

## Requires Sandbox

neo4j, codeql, joern
