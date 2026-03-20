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
TARGET_BINARY=/path/to/target /bash_tools/coverage/run-coverage/scripts/run_coverage.sh <testcase_path>
```

## Parameters

### testcase_path (required, positional position 0)

**Type**: `str`

The absolute path to the testcase file (must be in /shared).

### TARGET_BINARY (required, env var)

**Type**: `str`

Path to the target binary to run (must be executable).

## Return Value

Returns text output with a coverage summary.

## Requires Sandbox

coverage

## Timeout

240 seconds
