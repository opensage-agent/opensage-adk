---
name: show-coverage-all
description: Show aggregated code coverage results across all executed testcases within the sandbox environment.
should_run_in_sandbox: coverage
returns_json: false

---

# Show Coverage (All Testcases)

Show aggregated code coverage across all executed testcases.

## Usage

```bash
/bash_tools/coverage/show-coverage-all/scripts/show_coverage.sh <function_name> [file_path]
```

## Parameters

### function_name (required, positional position 0)

**Type**: `str`

The name of the function.

### file_path (optional, positional position 1)

**Type**: `str`

The absolute path to the file of the function.

### TARGET_BINARY (required, env var)

**Type**: `str`

Path to the target binary to show coverage for.

## Return Value

Returns text output with coverage details from `llvm-cov show`.

## Requires Sandbox

coverage

## Timeout

600 seconds
