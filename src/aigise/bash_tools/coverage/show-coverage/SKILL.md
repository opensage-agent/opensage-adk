---
name: show-coverage
description: Show code coverage results for a specified file and testcase within the sandbox environment.
should_run_in_sandbox: coverage
returns_json: false

---

# Show Coverage

Tool to show code coverage results for a specified file and testcase.

## Usage

```bash
scripts/show_coverage.sh <testcase_id> <function_name> [file_path]
```

## Parameters

### testcase_id (required, positional position 0)

**Type**: `str`

The id of the testcase.

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
