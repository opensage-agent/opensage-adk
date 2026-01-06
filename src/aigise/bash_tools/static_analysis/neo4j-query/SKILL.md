---
name: neo4j-query
description: Tool to run a custom Neo4j query against the code property graph.
should_run_in_sandbox: main
returns_json: false

---

# Neo4j Query Tool

Tool to run a custom Neo4j query against the code property graph.

## Usage

```bash
python3 scripts/neo4j_query.py "query_string" --params '{"key": "value"}'
```

With optional Neo4j connection parameters:

```bash
python3 scripts/neo4j_query.py "query_string" --params '{"key": "value"}' --neo4j-host "IP" --neo4j-port 7687
```

## Parameters

- `query`: The Cypher query string to execute.
- `params`: (Optional) JSON string of parameters for the query.
- `neo4j-host`: (Optional) IP address of Neo4j container. Defaults to `NEO4J_HOST` environment variable.
- `neo4j-port`: (Optional) Bolt port of Neo4j container. Defaults to `NEO4J_PORT` environment variable or 7687.
- `neo4j-user`: (Optional) Neo4j user. Defaults to `NEO4J_USER` environment variable or "neo4j".
- `neo4j-password`: (Optional) Neo4j password. Defaults to `NEO4J_PASSWORD` environment variable.
- `neo4j-database`: (Optional) Database name. Defaults to "neo4j".

**Note:** Neo4j connection parameters are automatically read from environment variables set in `~/.bashrc` by `Neo4jInitializer`. You only need to specify them explicitly if you want to override the defaults.

## Return Value

Returns plain text output listing query results with their field names and values.

## Requires Sandbox

neo4j, codeql, joern
