---
name: static_analysis
description: "Static code analysis tools using Joern and Neo4j. These tools perform advanced code analysis including control flow analysis, data flow analysis, call graph traversal, and program slicing. Available tools: joern-query, joern-slice, search-function, get-caller, get-callee, get-call-paths-to-function.
"
---

# Static Analysis Tools

Category of tools for advanced static code analysis using Joern and Neo4j. These tools provide sophisticated code analysis capabilities including call graph analysis, program slicing, control flow analysis, and graph-based queries.

## Available Tools

- **joern-query**: Execute Joern queries for code analysis
- **joern-slice**: Perform program slicing to extract relevant code paths
- **search-function**: Search for functions matching specific criteria
- **get-caller**: Get callers of a specific function
- **get-callee**: Get callees (functions called by) a specific function
- **get-call-paths-to-function**: Find all call paths leading to a function


## Usage

These tools work with joern and main sandbox types, depending on the specific tool.

The graph data lives in the `analysis` Neo4j database, populated by Joern/CodeQL initializers when the `joern` / `codeql` sandboxes start. Schema:

- Node `METHOD` — one node per function/method. Properties include `name`, `id`, file/line info.
- Node `TESTCASE` — one node per testcase (populated by coverage tools).
- Relationship `(METHOD)-[:CG_CALL]->(METHOD)` — direct call edges in the call graph.
- Relationship `(METHOD)-[:CG_MAYBE_INDIRECT_CALL]->(METHOD)` — indirect/virtual call edges (best-effort, may include false positives).
- Relationship `(TESTCASE)-[:COVERS]->(METHOD)` — coverage edges (populated by `coverage` tools).

The high-level skills below wrap common queries; for ad-hoc Cypher, use the `neo4j-query` skill against `--database analysis`.

## Common Use Cases

- Analyzing call graphs and control flow
- Finding all paths to a function or vulnerability
- Performing program slicing for focused analysis
- Querying code structure using graph databases
- Identifying function dependencies and relationships
- Static analysis for security vulnerability detection

## Requires Sandbox

joern, main, neo4j, codeql
