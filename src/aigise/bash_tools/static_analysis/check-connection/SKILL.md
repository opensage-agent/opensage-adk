---
name: check-connection
description: Tool to test connection to Neo4j database and run a simple query verification.
should_run_in_sandbox: main
returns_json: false

---

# Check Connection Tool

Tool to test connection to Neo4j database and run a simple query verification.
It attempts to connect to the specified Neo4j instance and execute a basic query (e.g. `RETURN 1`).

## Usage

```bash
python3 scripts/check_connection.py
```

With optional Neo4j connection parameters:

```bash
python3 scripts/check_connection.py --neo4j-host "IP" --neo4j-port 7687
```

## Parameters

- `neo4j-host`: (Optional) IP address of Neo4j container. Defaults to `NEO4J_HOST` environment variable.
- `neo4j-port`: (Optional) Bolt port of Neo4j container. Defaults to `NEO4J_PORT` environment variable or 7687.
- `neo4j-user`: (Optional) Neo4j user. Defaults to `NEO4J_USER` environment variable or "neo4j".
- `neo4j-password`: (Optional) Neo4j password. Defaults to `NEO4J_PASSWORD` environment variable.
- `neo4j-database`: (Optional) Database name. Defaults to "neo4j".

**Note:** Neo4j connection parameters are automatically read from environment variables set in `~/.bashrc` by `Neo4jInitializer`. You only need to specify them explicitly if you want to override the defaults.

## Return Value

Returns text output:
- On success: "Neo4j connection successful" followed by test query result
- On failure: "Neo4j connection failed: <error message>" and exits with code 1

## Requires Sandbox

neo4j
