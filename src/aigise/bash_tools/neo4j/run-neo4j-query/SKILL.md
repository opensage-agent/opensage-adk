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

## Parameters

### query (required, positional position 0)

**Type**: `str`

The Cypher query string to execute.

### params (optional)

**Type**: `str`

Optional JSON object string for query parameters. Default: `{}`.

### database (optional)

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
