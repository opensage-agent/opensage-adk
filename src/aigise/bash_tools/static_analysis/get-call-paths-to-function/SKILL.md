---
name: get-call-paths
description: Get a path in the call graph from a source function to a specified destination function in the codebase.
should_run_in_sandbox: main
returns_json: false

---

# Get Call Paths Tool

Get a path in the call graph from a source function to a specified destination function in the codebase.
Note that LLVMFuzzerTestOneInput is the default source function name if not provided.

## Usage

```bash
python3 scripts/get_call_paths_to_function.py "dst_function_name" --dst-file "path/to/dst.py" --src-function "src_func" --src-file "path/to/src.py"
```

With optional Neo4j connection parameters:

```bash
python3 scripts/get_call_paths_to_function.py "dst_function_name" --dst-file "path/to/dst.py" --src-function "src_func" --src-file "path/to/src.py" --neo4j-host "IP" --neo4j-port 7687
```

## Parameters

- `dst_function_name`: The name of the destination function to search for.
- `dst-file`: (Optional) The file path where the destination function is defined.
- `src-function`: (Optional) The name of the source function. Defaults to "LLVMFuzzerTestOneInput".
- `src-file`: (Optional) The file path where the source function is defined.
- `neo4j-host`: (Optional) IP address of Neo4j container. Defaults to `NEO4J_HOST` environment variable.
- `neo4j-port`: (Optional) Bolt port of Neo4j container. Defaults to `NEO4J_PORT` environment variable or 7687.
- `neo4j-user`: (Optional) Neo4j user. Defaults to `NEO4J_USER` environment variable or "neo4j".
- `neo4j-password`: (Optional) Neo4j password. Defaults to `NEO4J_PASSWORD` environment variable.
- `neo4j-database`: (Optional) Database name. Defaults to "neo4j".

**Note:** Neo4j connection parameters are automatically read from environment variables set in `~/.bashrc` by `Neo4jInitializer`. You only need to specify them explicitly if you want to override the defaults.

## Return Value

Returns a JSON object with key "result" pointing to a list of path information.

## Requires Sandbox

neo4j, codeql, joern
