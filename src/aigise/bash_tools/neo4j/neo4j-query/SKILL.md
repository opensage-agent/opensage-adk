---
name: neo4j-query
description: Run an arbitrary Cypher query against Neo4j and return JSON records.
should_run_in_sandbox: main
returns_json: true
---

# Neo4j Query

Run an arbitrary Cypher query against Neo4j and return JSON records.

## Usage

```bash
python3 /bash_tools/neo4j/neo4j-query/scripts/neo4j_query.py "MATCH (n) RETURN n LIMIT 1"
```

```bash
python3 /bash_tools/neo4j/neo4j-query/scripts/neo4j_query.py \
  "MATCH (n {name: \$name}) RETURN n" \
  --params '{"name":"Alice"}'
```

```bash
python3 /bash_tools/neo4j/neo4j-query/scripts/neo4j_query.py \
  "SHOW DATABASES YIELD name RETURN name" \
  --database "analysis"
```

## Parameters

### query (required, positional position 0)

**Type**: `str`

Cypher query string to execute.

### --params (optional, named parameter)

**Type**: `str`

Optional JSON object string for query parameters. Default: `{}`.

### --database (optional, named parameter)

**Type**: `str`

Neo4j database name (optional). If omitted, defaults to `NEO4J_DATABASE` env var
or `analysis`.

## Return Value

```json
{
  "records": [
    {"key": "value"}
  ]
}
```

## Requires Sandbox

neo4j
