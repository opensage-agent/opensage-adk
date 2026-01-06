---
name: check-fuzzing-stats
description: Check fuzzing coverage and statistics. Provides comprehensive information about the current fuzzing campaign, including whether the output directory exists, parsed AFL++ fuzzer statistics (executions, exec_speed, paths_total, etc.), and analysis results (crashes found, unique crashes, etc.). Use this tool to monitor the progress of a fuzzing campaign and check if any crashes have been discovered. This tool is designed for use in **fuzz** sandbox environments where AFL++ is configured.
should_run_in_sandbox: fuzz
returns_json: false

---

# Check Fuzzing Stats

Monitor AFL++ fuzzing campaigns by retrieving comprehensive statistics and crash information.

## Usage

Execute the script to check the current fuzzing campaign status:

```bash
scripts/check_fuzzing_stats.sh
```

## Parameters

None. The script automatically locates and analyzes the fuzzing output directory at `/fuzz/out`.

## Return Value

Returns text output with fuzzing statistics:
- Output directory status
- Fuzzer statistics from `fuzzer_stats` file (if available)
- Results summary including crashes found and unique crashes

## Requires Sandbox

fuzz

## Timeout

Default timeout: 10 seconds
