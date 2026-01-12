---
name: run-fuzzing-campaign
description: Run a fuzzing campaign using AFL++ with optional seeds and a custom mutator.
should_run_in_sandbox: fuzz
returns_json: false

---

# Run Fuzzing Campaign

Run an AFL++ fuzzing campaign.

## Usage

```bash
scripts/run_fuzzing_campaign.sh target_binary 180
```

```bash
scripts/run_fuzzing_campaign.sh target_binary 180 /path/to/seed1.txt /path/to/seed2.txt
```

```bash
scripts/run_fuzzing_campaign.sh target_binary 180 /path/to/seed.txt --custom_mutator_path /fuzz/mutator/custom_mutator.py
```

```bash
scripts/run_fuzzing_campaign.sh target_binary 180 /path/to/seed.txt --reset_output
```

## Parameters

### fuzz_target (required, positional position 0)

**Type**: `str`

Fuzz target binary name (expected at `/out/<fuzz_target>`).

### duration_seconds (required, positional position 1)

**Type**: `int`

Fuzzing duration in seconds (e.g., `180`).

### seed_paths (optional, positional position 2+)

**Type**: `list` of strings

Optional seed file/dir paths.

### --custom_mutator_path (optional, named parameter)

**Type**: `str`

Optional path to a custom mutator script.

### --reset_output (optional, flag)

**Type**: `bool` (default: `false`)

Reset output and start fresh.

## Return Value

Returns text output (summary of fuzzing results).

## Requires Sandbox

fuzz

## Timeout

200 seconds
