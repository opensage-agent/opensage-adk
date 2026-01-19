---
name: extract-crashes
description: Extract crash inputs from fuzzing output into a target directory.
should_run_in_sandbox: fuzz
returns_json: false

---

# Extract Crashes

Copy crash input files from fuzzing output to a target directory.

## Usage

```bash
/bash_tools/fuzz/extract-crashes/scripts/extract_crashes.sh /path/to/target_dir
```

```bash
/bash_tools/fuzz/extract-crashes/scripts/extract_crashes.sh /path/to/target_dir crash_file1 crash_file2
```

## Parameters

### target_dir (required, positional position 0)

**Type**: `str`

Target directory to copy crashes into.

### crash_names (optional, positional position 1+)

**Type**: `list` of strings

Optional crash file names to extract. If omitted, extracts all crashes.

## Return Value

Returns text output indicating success/failure.

## Requires Sandbox

fuzz

## Timeout

30 seconds
