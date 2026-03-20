---
name: retrieval
description: "Code retrieval tools. This category currently includes symbol lookup and function listing (via Neo4j-backed queries). Available tools: search-symbol, list-functions."
---

# Retrieval Tools

Category of tools for searching and retrieving code structure information (symbols, functions).

## Available Tools

- **search-symbol**: Search the codebase for the definition of a given symbol (ctags-style output)
- **list-functions**: List functions in a file via Neo4j queries over the code property graph

## Usage

Use `search-symbol` to quickly locate definitions, and `list-functions` to enumerate functions with locations (requires Neo4j-backed graph data).

## Common Use Cases

- Finding where a symbol is defined
- Locating candidate functions for further analysis
- Enumerating functions in a file with start/end line numbers

## Requires Sandbox

main, neo4j, codeql, joern
