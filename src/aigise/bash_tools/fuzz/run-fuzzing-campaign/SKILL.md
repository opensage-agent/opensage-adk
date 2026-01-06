---
name: run-fuzzing-campaign
description: Run a fuzzing campaign using AFL++. Sets up a complete AFL++ fuzzing campaign, including directory setup, seed file management, custom mutator support, and result analysis. The fuzzing runs for the specified duration (typically 180 seconds / 3 minutes). If no seeds are provided, it will continue with previous fuzzing campaign state, or use a default seed ("1234") if no previous state exists. You should normally create a seed file and indicate its path. The fuzz_target should be the name of the binary compiled with AFL++ instrumentation (usually found in /out directory). If you need to use a custom mutator provided as code string, you should first create the mutator file in the container (e.g., using bash_tool to write the file to /fuzz/mutator/custom_mutator.py), then pass the path to this tool. This tool is designed for use in **fuzz** sandbox environments with AFL++ installed.
should_run_in_sandbox: fuzz
returns_json: false

---

# Run Fuzzing Campaign

Execute a complete AFL++ fuzzing campaign with configurable duration, seeds, and custom mutators.

## Usage

Basic fuzzing campaign:

```bash
scripts/run_fuzzing_campaign.sh target_binary 180
```

With seed files:

```bash
scripts/run_fuzzing_campaign.sh target_binary 180 /path/to/seed1.txt /path/to/seed2.txt
```

With custom mutator:

```bash
scripts/run_fuzzing_campaign.sh target_binary 180 /path/to/seed.txt --custom_mutator_path /fuzz/mutator/custom_mutator.py
```

Reset and start fresh:

```bash
scripts/run_fuzzing_campaign.sh target_binary 180 /path/to/seed.txt --reset_output
```

## Parameters

### fuzz_target (required, positional position 0)

**Type**: `str`

Name of the fuzzing target binary. This should be the binary compiled with AFL++ instrumentation. The binary is expected to be located at `/out/{fuzz_target}`.

**Tip**: You can retrieve the target name from the environment variable `FUZZ_TARGET`.

**Example**: `harfbuzz-1.3.2`, `libxml2-v2.9.2`

### duration_seconds (required, positional position 1)

**Type**: `int`

Fuzzing duration in seconds. Typical values are 180 (3 minutes) or longer for more thorough fuzzing.

**Example**: `180`, `300`, `600`

### seed_paths (optional, positional position 2+)

**Type**: `list` of strings

Optional list of paths to seed input files or directories. These will be copied to `/fuzz/in` directory. If not provided:
- If previous fuzzing state exists (`/fuzz/out/fuzzer_stats`), continues from that state
- Otherwise, uses a default seed ("1234")

**Example**: `/workspace/seeds/seed1.txt`, `/tmp/initial_input.dat`

### --custom_mutator_path (optional, named parameter)

**Type**: `str`

Optional path to custom mutator Python script in the container. The mutator should define a function `fuzz(buf, add_buf, max_size)` that returns a mutated bytearray. The file should already exist in the container at this path.

**Example**: `--custom_mutator_path /fuzz/mutator/custom_mutator.py`

### --reset_output (optional, flag)

**Type**: `bool` (default: `false`)

If specified, reset the output directory even if it exists, starting a fresh fuzzing campaign. If not specified, will continue from previous state if available.

**Example**: `--reset_output`

## Return Value

Returns text output with fuzzing results:
- Fuzz target name
- Duration in minutes
- Results including crashes found, unique crashes, executions, and execution speed
- On error: Error message to stderr and exits with code 1

## Behavior

1. **Setup Phase**:
   - Checks if `/fuzz/out/fuzzer_stats` exists to determine if continuing previous campaign
   - If `--reset_output` is specified or no previous state exists, cleans `/fuzz/out`
   - Cleans and recreates `/fuzz/in` directory
   - Creates `/fuzz/mutator` directory

2. **Seed Management**:
   - If seed paths provided: copies all seeds to `/fuzz/in/`
   - If no seeds and no previous state: creates default seed with content "1234"
   - If no seeds but previous state exists: continues with existing corpus

3. **Fuzzing Execution**:
   - Sets `AFL_NO_UI=1` for non-interactive mode
   - If custom mutator specified: sets `PYTHONPATH` and `AFL_PYTHON_MODULE`
   - Runs `afl-fuzz -i /fuzz/in -o /fuzz/out /out/{fuzz_target}` with timeout

4. **Results Analysis**:
   - Counts crash files in crashes directory
   - Extracts `execs_done` and `execs_per_sec` from `fuzzer_stats`
   - Returns comprehensive JSON report

## Environment Variables

- **AFL_NO_UI**: Set to `1` automatically for non-interactive fuzzing
- **PYTHONPATH**: Set to `/fuzz/mutator` when custom mutator is used
- **AFL_PYTHON_MODULE**: Set to `custom_mutator` when custom mutator is used

## Requires Sandbox

fuzz

## Timeout

Default timeout: 200 seconds (allows for 180-second fuzzing duration plus overhead)
