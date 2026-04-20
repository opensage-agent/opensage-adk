---
name: retrieval
description: "Code retrieval tools for symbol lookup and function listing via Neo4j-backed queries. Searches for function/symbol definitions and lists functions with file paths and line ranges. Use when the user asks to find a function definition, locate a symbol, list all functions in a file, look up where something is defined, or navigate codebase structure. Available tools: search-symbol, list-functions."
---

# Retrieval Tools

Search and retrieve code structure information — symbol definitions and function listings — backed by Neo4j graph queries.

## Available Tools

| Tool | Purpose | When to use |
|------|---------|-------------|
| **search-symbol** | Find the definition of a symbol in the codebase | User asks "where is X defined?", needs to locate a function, class, or variable definition |
| **list-functions** | List all functions in a file with line ranges | User wants to browse functions in a file, identify candidate functions for analysis |

## Workflow

1. **Find a definition**: Run `search-symbol` with the symbol name to get ctags-style output showing file path and location
2. **Browse functions**: Run `list-functions` with a file path to enumerate all functions with their start/end line numbers
3. **Combine with analysis**: Feed results into static analysis tools (get-caller, get-callee) for deeper investigation

## Requires Sandbox

main, neo4j, codeql, joern
