---
name: extract-crashes
description: Extract crash inputs from fuzzing output. Extracts crash files from the AFL++ fuzzing output directory to a specified target directory. You can extract specific crashes by name or all crashes at once. The target directory will be created if it doesn't exist. This tool is designed for use in **fuzz** sandbox environments where AFL++ has been run.
should_run_in_sandbox: fuzz
returns_json: false

---

# Extract Crashes

Extract crash input files from AFL++ fuzzing output to a target directory for analysis.

## Usage

Extract all crashes:

```bash
scripts/extract_crashes.sh /path/to/target_dir
```

Extract specific crashes:

```bash
scripts/extract_crashes.sh /path/to/target_dir crash_file1 crash_file2
```

## Parameters

### target_dir (required, positional position 0)

**Type**: `str`

Directory in the container to copy crashes to. The directory will be created if it doesn't exist.

**Example**: `/tmp/crashes`, `/workspace/analysis/crashes`

### crash_names (optional, positional position 1+)

**Type**: `list` of strings

Optional list of crash file names to extract. If not provided, extracts all crashes from the crashes directory.

**Example**: `id:000000,sig:06,src:000000`, `id:000001,sig:11,src:000002`

## Return Value

Returns text output:
- On success: "Crashes extracted successfully to: <target_dir>"
- On error: Error message to stderr and exits with non-zero code

## Behavior

1. Locates the AFL++ crashes directory at `/fuzz/out/*/crashes`
2. Creates the target directory if it doesn't exist
3. If no crash names specified: copies all files from crashes directory to target
4. If crash names specified: copies only the specified crash files
5. Warns (to stderr) if a specified crash file is not found, but continues processing other files

## Requires Sandbox

fuzz

## Timeout

Default timeout: 30 seconds
