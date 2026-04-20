---
name: find-testcases-covering-function
description: "Query Neo4j to find which testcases (TESTCASE nodes) cover a given function (METHOD node). Returns testcase IDs as JSON. Use when the user asks which tests cover a function, wants to find test coverage for a specific method, needs to identify testcases exercising a code path, or says 'which tests hit this function'."
should_run_in_sandbox: main
returns_json: true
---

# find-testcases-covering-function

Query Neo4j to find testcase IDs that cover a given function. Useful for identifying which tests exercise a specific method and for coverage-guided analysis.

## Usage

```bash
python3 /bash_tools/coverage/find-testcases-covering-function/scripts/find_testcases_covering_function.py "FUNCTION_NAME" \
  --file_path "/absolute/path/to/file" \
  --database "analysis"
```

### Example

```bash
# Find all testcases covering the "parse_input" function
python3 /bash_tools/coverage/find-testcases-covering-function/scripts/find_testcases_covering_function.py "parse_input" \
  --file_path "/src/parser.c" \
  --database "analysis"
```

## Requires Sandbox

neo4j, main

## Parameters

### function_name (required, positional position 0)

**Type**: `str`

Function name to query. Must match the METHOD node name in the Neo4j graph.

### --file_path (optional, named parameter)

**Type**: `str`

Absolute file path to disambiguate when multiple functions share the same name across files.

### --database (optional, named parameter)

**Type**: `str`

Neo4j database name to query. Defaults to `"analysis"`.

## Return Value

```json
{
  "testcase_ids": ["<id1>", "<id2>"]
}
```

Returns JSON with `testcase_ids` array. The array is empty if no testcases cover the function.
