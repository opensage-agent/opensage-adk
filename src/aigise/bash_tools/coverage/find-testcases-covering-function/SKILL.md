---
name: find_testcases_covering_function
description: Find testcase IDs (TESTCASE nodes) that cover a given function (METHOD node) in Neo4j.
should_run_in_sandbox: main
returns_json: true
---

# find_testcases_covering_function

Find testcase IDs that cover a given function.

## Usage

```bash
python3 /bash_tools/coverage/find-testcases-covering-function/scripts/find_testcases_covering_function.py "FUNCTION_NAME" \
  --file_path "/absolute/path/to/file" \
  --database "analysis"
```

## Requires Sandbox

neo4j, main

## Parameters

### function_name (required, positional position 0)

**Type**: `str`

Function name to query.

### --file_path (optional, named parameter)

**Type**: `str`

Optional file path to disambiguate results.

## Return Value

```json
{
  "testcase_ids": ["<id1>", "<id2>"]
}
```

Returns JSON with `testcase_ids` (may be empty).
