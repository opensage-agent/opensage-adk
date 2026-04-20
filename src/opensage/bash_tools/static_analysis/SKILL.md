---
name: static-analysis
description: "Static code analysis tools using Joern and Neo4j for call graph traversal, program slicing, control flow analysis, and code property graph queries. Use when the user asks about static analysis, call graphs, function callers/callees, program slicing, vulnerability detection, code property graphs, or Joern queries. Available tools: joern-query, joern-slice, search-function, get-caller, get-callee, get-call-paths-to-function."
---

# Static Analysis Tools

Advanced static code analysis using Joern and Neo4j. Provides call graph analysis, program slicing, control flow analysis, and graph-based code queries.

## Available Tools

| Tool | Purpose | Key use |
|------|---------|---------|
| **joern-query** | Execute Joern/CPG queries | Ad-hoc code property graph traversal |
| **joern-slice** | Program slicing | Extract code paths relevant to a function |
| **search-function** | Function search | Locate functions by name in the codebase |
| **get-caller** | Caller lookup | Find which functions call a given function |
| **get-callee** | Callee lookup | Find which functions a given function calls |
| **get-call-paths-to-function** | Call path analysis | Trace all call chains leading to a target function |

## Workflow

1. **Identify target**: Use `search-function` to locate the function of interest
2. **Explore call graph**: Use `get-caller` / `get-callee` to understand direct dependencies
3. **Trace paths**: Use `get-call-paths-to-function` to find indirect call chains to a vulnerable or critical function
4. **Slice code**: Use `joern-slice` to extract the relevant program slice for focused analysis
5. **Custom queries**: Use `joern-query` for advanced CPG queries not covered by the other tools

## Requires Sandbox

joern, main, neo4j, codeql
