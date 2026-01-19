---
name: simplified-python-fuzzer
description: Run a Python fuzzer script (provided as a string) for a fixed duration.
should_run_in_sandbox: main
returns_json: false

---

# Simplified Python Fuzzer

Execute a Python fuzzer script (provided inline) and collect results.

## Usage

```bash
/bash_tools/fuzz/simplified-python-fuzzer/scripts/simplified_python_fuzzer.sh "$(cat fuzzer_script.py)"
```

```bash
/bash_tools/fuzz/simplified-python-fuzzer/scripts/simplified_python_fuzzer.sh "$(cat fuzzer_script.py)" 300
```

## Parameters

### script (required, positional position 0)

**Type**: `str`

Python code as a string (executed with `python3`).

### duration_seconds (optional, positional position 1)

**Type**: `int` (default: `180`)

Duration to run in seconds.

## Return Value

Returns text output with execution status and any detected crash files.

## Requires Sandbox

main

## Timeout

250 seconds
