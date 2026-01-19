---
name: run-fuzzing-campaign
description: Run a fuzzing campaign using AFL++ with optional seeds; supports `--custom_mutator_path` (you can write your own custom mutator and use this to execute).
should_run_in_sandbox: fuzz
returns_json: false

---

# Run Fuzzing Campaign

Run an AFL++ fuzzing campaign.

This tool supports **custom mutators** via `--custom_mutator_path`. You can
also **write your own** custom mutator (Python) and pass it in.

## Usage

```bash
/bash_tools/fuzz/run-fuzzing-campaign/scripts/run_fuzzing_campaign.sh target_binary 180
```

```bash
/bash_tools/fuzz/run-fuzzing-campaign/scripts/run_fuzzing_campaign.sh target_binary 180 /path/to/seed1.txt /path/to/seed2.txt
```

```bash
/bash_tools/fuzz/run-fuzzing-campaign/scripts/run_fuzzing_campaign.sh target_binary 180 /path/to/seed.txt --custom_mutator_path /fuzz/mutator/custom_mutator.py
```

```bash
/bash_tools/fuzz/run-fuzzing-campaign/scripts/run_fuzzing_campaign.sh target_binary 180 /path/to/seed.txt --reset_output
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

## Custom Mutator

If you provide `--custom_mutator_path`, AFL++ will load the mutator from that
path. A common convention is to place it in the fuzz sandbox, e.g.:

```bash
/bash_tools/fuzz/run-fuzzing-campaign/scripts/run_fuzzing_campaign.sh target_binary 180 /path/to/seed.txt \
  --custom_mutator_path /fuzz/mutator/custom_mutator.py
```

You can implement your own `custom_mutator.py` as part of your workflow and
iterate on it during fuzzing.

## Return Value

Returns text output (summary of fuzzing results).

## Requires Sandbox

fuzz

## Timeout

200 seconds
