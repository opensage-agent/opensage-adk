---
name: run-coverage
description: Run code coverage analysis on a specified file within the sandbox environment.
should_run_in_sandbox: coverage
returns_json: false

---

# Run Coverage

Tool to run code coverage analysis on a specified file within the sandbox environment.
The testcase_path should be under the /shared directory.

## Usage

```bash
scripts/run_coverage.sh <testcase_path>
```

## Parameters

### testcase_path (required, positional position 0)

**Type**: `str`

The absolute path to the testcase file (must be in /shared).

## Return Value

Returns text output with coverage summary (first and last lines of the coverage report).
Note: This bash version runs the coverage collection. Neo4j upload requires the python host tool.

## Requires Sandbox

coverage

## Timeout

60 seconds
