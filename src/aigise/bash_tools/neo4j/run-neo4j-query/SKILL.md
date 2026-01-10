---
name: run_neo4j_query
description: Run an arbitrary Cypher query against Neo4j and return JSON records.
should_run_in_sandbox: main
returns_json: true
---

# run_neo4j_query

Run an arbitrary Cypher query against Neo4j and return JSON results.

This tool is designed to run **standalone inside the sandbox** (from
`/bash_tools`), without any ADK tool context.

## Usage

Basic query:

```bash
scripts/run_neo4j_query.sh "MATCH (n) RETURN n LIMIT 1"
```

With parameters:

```bash
scripts/run_neo4j_query.sh \
  "MATCH (n {name: \$name}) RETURN n" \
  --params '{"name":"Alice"}'
```

With explicit database:

```bash
scripts/run_neo4j_query.sh \
  "SHOW DATABASES YIELD name RETURN name" \
  --database "analysis"
```

## Parameters

### query (required, positional position 0)

**Type**: `str`

The Cypher query string to execute.

### --params (optional, named parameter)

**Type**: `str`

Optional JSON object string for query parameters. Default: `{}`.

### --database (optional, named parameter)

**Type**: `str`

Neo4j database name. Default: environment `NEO4J_DATABASE` or `"analysis"`.

## Return Value

```json
{
  "records": [
    {"key": "value"}
  ]
}
```

## Timeout

60 seconds
