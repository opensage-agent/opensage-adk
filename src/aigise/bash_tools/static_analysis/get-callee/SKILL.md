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

With optional Neo4j connection parameters:

```bash
python3 scripts/get_callee.py "function_name" --file-path "relative/path/to/file.py" --neo4j-host "IP" --neo4j-port 7687
```

## Parameters

- `function_name`: The name of the function to search for.
- `file_path`: (Optional) The file path where the function is defined.
- `neo4j-host`: (Optional) IP address of Neo4j container. Defaults to `NEO4J_HOST` environment variable.
- `neo4j-port`: (Optional) Bolt port of Neo4j container. Defaults to `NEO4J_PORT` environment variable or 7687.
- `neo4j-user`: (Optional) Neo4j user. Defaults to `NEO4J_USER` environment variable or "neo4j".
- `neo4j-password`: (Optional) Neo4j password. Defaults to `NEO4J_PASSWORD` environment variable.
- `neo4j-database`: (Optional) Database name. Defaults to "neo4j".

**Note:** Neo4j connection parameters are automatically read from environment variables set in `~/.bashrc` by `Neo4jInitializer`. You only need to specify them explicitly if you want to override the defaults.

## Return Value

Returns plain text output listing callees with their function names, file paths, line numbers, and call types.

## Requires Sandbox

neo4j, codeql, joern
