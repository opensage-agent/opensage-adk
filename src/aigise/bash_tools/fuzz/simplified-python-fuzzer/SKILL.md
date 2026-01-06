---
name: simplified-python-fuzzer
description: Run a simplified Python fuzzer script that mutates one seed, feeds it to the target program, and monitors whether the target program crashes. The script should follow this design - 1) Load or create a seed file in /tmp, 2) Mutate the seed file considering format and grammar, 3) Feed the mutated seed to the target program, 4) Save crash and input to /tmp if crash occurs, 5) Repeat until crash. The script will be run with python3 for the specified duration (default 180 seconds). The script should not print output, but save crashes to files. Returns text output with execution results, exit code, crash files found, and crash details. This tool is designed for use in **main** sandbox environments where you have a target binary to test.
should_run_in_sandbox: main
returns_json: false

---

# Simplified Python Fuzzer

Execute a custom Python fuzzing script that mutates seeds and tests a target program for crashes.

## Usage

Run with default 180-second duration:

```bash
scripts/simplified_python_fuzzer.sh "$(cat fuzzer_script.py)"
```

Run with custom duration:

```bash
scripts/simplified_python_fuzzer.sh "$(cat fuzzer_script.py)" 300
```

## Parameters

### script (required, positional position 0)

**Type**: `str`

The Python fuzzer script code as a string. The script will be written to `/tmp/fuzzer.py` and executed with `python3`.

**Script Design Guidelines**:

The fuzzer script should follow this pattern:

1. **Load or create a seed file** in `/tmp`
2. **Mutate the seed** considering format and grammar requirements
3. **Feed the mutated seed** to the target program
4. **Monitor for crashes** and save crash information
5. **Repeat** until a crash is found or timeout occurs

**Important**:
- The script should NOT print output during execution
- Crash information should be saved to files (e.g., `/tmp/crash_*.txt` or `/tmp/crash.txt`)
- The script should handle the target program's execution and crash detection

**Example Script Structure**:

```python
import subprocess
import random
import os

# Load or create seed
seed_path = "/tmp/seed.txt"
if not os.path.exists(seed_path):
    with open(seed_path, "w") as f:
        f.write("initial seed data")

iteration = 0
while True:
    # Read seed
    with open(seed_path, "rb") as f:
        data = bytearray(f.read())

    # Mutate seed
    if len(data) > 0:
        pos = random.randint(0, len(data) - 1)
        data[pos] = random.randint(0, 255)

    # Write mutated input
    mutated_path = f"/tmp/input_{iteration}.txt"
    with open(mutated_path, "wb") as f:
        f.write(data)

    # Test target program
    try:
        result = subprocess.run(
            ["/out/target_binary", mutated_path],
            timeout=1,
            capture_output=True
        )

        # Check for crash (non-zero exit code)
        if result.returncode != 0:
            # Save crash information
            with open(f"/tmp/crash_{iteration}.txt", "w") as f:
                f.write(f"Crash found at iteration {iteration}\n")
                f.write(f"Exit code: {result.returncode}\n")
                f.write(f"Input file: {mutated_path}\n")
            break
    except subprocess.TimeoutExpired:
        pass  # Timeout is not a crash

    iteration += 1
```

### duration_seconds (optional, positional position 1)

**Type**: `int` (default: `180`)

Duration to run the fuzzer script in seconds. Default is 180 seconds (3 minutes).

**Example**: `180`, `300`, `600`

## Return Value

Returns text output with execution results and crash information:
- Execution status and exit code
- Script output (if any)
- List of crash files found (if any)
- Details of up to 5 crash files (first 100 lines each)

## Behavior

1. Writes the provided script content to `/tmp/fuzzer.py`
2. Executes the script with `timeout {duration_seconds}s python3 /tmp/fuzzer.py`
3. After execution (or timeout), searches `/tmp` for crash files matching patterns:
   - `crash_*`
   - `crash.txt`
4. Reads up to 5 crash files (first 100 lines each) and includes in response
5. Returns JSON with all results

## Crash File Detection

The tool automatically detects crash files in `/tmp` with these naming patterns:
- `crash_*.txt` (e.g., `crash_0.txt`, `crash_iteration_5.txt`)
- `crash.txt`

Ensure your fuzzer script saves crash information using these naming conventions.

## Requires Sandbox

main, fuzz

## Timeout

Default timeout: 250 seconds (allows for 180-second fuzzing duration plus overhead)
