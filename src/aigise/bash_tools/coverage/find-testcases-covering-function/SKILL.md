---
name: find_testcases_covering_function
description: Find testcase IDs (TESTCASE nodes) that cover a given function (METHOD node) in Neo4j.
should_run_in_sandbox: main
returns_json: true
---

# find_testcases_covering_function

This skill queries Neo4j (database: `analysis` by default) for testcase IDs that
cover a given function.

It is designed to run **standalone inside the sandbox** (from `/bash_tools`),
without any ADK tool context.

## Requires Sandbox

neo4j, main

## Parameters

### function_name (required)

**Type**: `str`

Function name to match against `m.NAME` in Neo4j.

### file_path (optional)

**Type**: `str`

Optional absolute file path to disambiguate by `m.FILENAME` (substring match).

## Return Value

```json
{
  "testcase_ids": ["<id1>", "<id2>"]
}
```

If Neo4j is not configured/reachable, it returns an empty list and prints a
warning to stderr.
