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
TARGET_BINARY=/path/to/target scripts/run_coverage.sh <testcase_path>
```

## Parameters

### testcase_path (required, positional position 0)

**Type**: `str`

The absolute path to the testcase file (must be in /shared).

## Notes

- **TARGET_BINARY (required)**: Path to the target binary to run (must be executable).
- **Testcase ID**: The script computes `testcase_id = md5(realpath(testcase_path))` (hash of the
  canonical path string, not file contents) and stores outputs under:
  `/shared/.aigise/coverage/<id[:2]>/<id[2:4]>/<id>/`
- **LLVM coverage requirement**: The target binary must be built with LLVM
  profile+coverage mapping (e.g. `-fprofile-instr-generate -fcoverage-mapping`).
  The script validates this and fails early if missing.

## Return Value

Returns text output with coverage summary (first and last lines of the coverage report).
Note: This bash version runs the coverage collection. Neo4j upload requires the python host tool.

## Requires Sandbox

coverage

## Timeout

60 seconds
