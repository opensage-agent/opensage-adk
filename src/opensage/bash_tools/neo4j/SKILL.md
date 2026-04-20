---
name: neo4j
description: "Neo4j graph database tools for running Cypher queries and retrieving JSON records. Executes arbitrary Cypher queries against the code property graph for code analysis, dependency exploration, and node/relationship traversal. Use when the user asks to query Neo4j, run Cypher, explore graph data, retrieve node relationships, or interact with the code property graph. Available tools: neo4j-query."
---

# Neo4j Tools

Run Cypher queries against Neo4j graph databases and return results as JSON records. Powers code analysis workflows that depend on the code property graph.

## Available Tools

| Tool | Purpose | When to use |
|------|---------|-------------|
| **neo4j-query** | Execute Cypher queries, return JSON records | Any direct graph query — browsing nodes, traversing relationships, aggregating data |

## Workflow

1. **Query the graph**: Run `neo4j-query` with a Cypher statement to retrieve nodes, relationships, or aggregated data
2. **Interpret results**: Parse the returned JSON records for downstream analysis
3. **Combine with other tools**: Feed query results into static analysis or coverage tools for deeper investigation

### Example Queries

```cypher
-- List all METHOD nodes in a file
MATCH (m:METHOD) WHERE m.filename CONTAINS '/parser.c' RETURN m.name, m.lineNumber

-- Find all TESTCASE nodes covering a function
MATCH (t:TESTCASE)-[:COVERS]->(m:METHOD {name: 'parse_input'}) RETURN t.id
```

## Requires Sandbox

neo4j
