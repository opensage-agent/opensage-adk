---
name: show-coverage-one-testcase
description: Show code coverage results for a specified file and testcase within the sandbox environment.
should_run_in_sandbox: coverage
returns_json: false

---

# Show Coverage

Show coverage for a specified testcase and function.

## Usage

```bash
TARGET_BINARY=/path/to/target /bash_tools/coverage/show-coverage-one-testcase/scripts/show_coverage.sh <testcase_path> <function_name> [file_path]
```

## Parameters

### testcase_path (required, positional position 0)

**Type**: `str`

The absolute path to the testcase file (must be in /shared).

### TARGET_BINARY (required, env var)

**Type**: `str`

Path to the target binary to show coverage for.

### function_name (required, positional position 1)

**Type**: `str`

The name of the function.

### file_path (optional, positional position 2)

**Type**: `str`

The absolute path to the file of the function.

## Return Value

Returns text output with coverage details from llvm-cov show command.

## Requires Sandbox

coverage
