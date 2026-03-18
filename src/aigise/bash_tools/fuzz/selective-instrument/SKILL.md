---
name: selective-instrument
description: "Selective AFL++ instrumentation for directed fuzzing. Agent writes target functions/files to /fuzz/allowlist.txt, recompiles with AFL_LLVM_ALLOWLIST, then runs directed fuzzing to quickly reach the target region and collect characteristic seeds for further analysis or conventional fuzzing."
should_run_in_sandbox: fuzz
returns_json: false
---

# Selective Instrument

Directed fuzzing via AFL++ selective instrumentation. Only the specified
functions or source files are instrumented, so AFL++ coverage feedback is
limited to the target region. This makes the fuzzer converge quickly toward
inputs that exercise those specific code paths.

## Purpose

Selective instrumentation is a **directed fuzzing** technique:
1. Quickly discover inputs that **reach the target region** (e.g., a patched
   function, a suspected vulnerability)
2. Collect **characteristic seeds** — inputs that exercise the target code
3. Feed those seeds into a conventional full-instrumented fuzzer for deeper
   mutation-based exploration, or use them for further analysis (coverage,
   debugging, PoC generation)

The instrumented binary is written to `/out_selective` (not `/out`), so the
original full-instrumented build is preserved.

## Workflow

1. **Identify targets**: Use static analysis, patch diff, or code review to
   determine which functions or files to focus on.

2. **Write the allowlist**: Create `/fuzz/allowlist.txt` in the fuzz sandbox
   with one entry per line. Use `run_terminal_command` to write it:
   ```
   cat > /fuzz/allowlist.txt << 'EOF'
   fun:parse_header
   fun:decode_payload
   src:lib/parser.c
   EOF
   ```
   Allowlist syntax (AFL_LLVM_ALLOWLIST format):
   - `fun:<name>` — instrument a specific function (wildcards OK: `fun:parse_*`)
   - `src:<path>` — instrument all functions in a source file
   - `mod:<path>` — instrument all functions in files under a directory

3. **Recompile** with selective instrumentation:
   ```bash
   /bash_tools/fuzz/selective-instrument/scripts/selective_instrument.sh
   ```
   (Optional extra entries can be passed as arguments — they are appended to
   the existing allowlist file.)

4. **Run directed fuzzing** to collect seeds that reach the target:
   ```bash
   /bash_tools/fuzz/selective-instrument/scripts/run_selective_fuzz.sh <fuzz_target> <duration_seconds> [seed_paths...] [--custom_mutator_path <path>] [--reset_output]
   ```

5. **Use the collected seeds**: The fuzzing output (including seeds and
   crashes) is in `/fuzz/out_selective/`. Feed interesting seeds into the
   full-instrumented `run-fuzzing-campaign`, or use them for coverage
   analysis, debugging, or PoC generation.

## Tools

### selective_instrument.sh — Recompile with allowlist

Reads `/fuzz/allowlist.txt` (which the agent must create beforehand),
optionally appends extra entries from arguments, then recompiles the project
with `AFL_LLVM_ALLOWLIST` into `/out_selective`.

```bash
# Typical: agent already wrote /fuzz/allowlist.txt
/bash_tools/fuzz/selective-instrument/scripts/selective_instrument.sh

# Or append extra entries via arguments
/bash_tools/fuzz/selective-instrument/scripts/selective_instrument.sh "fun:extra_func"
```

### run_selective_fuzz.sh — Directed fuzzing

Runs AFL++ using the selectively instrumented binary from `/out_selective`.
Output goes to `/fuzz/out_selective/`.

```bash
/bash_tools/fuzz/selective-instrument/scripts/run_selective_fuzz.sh <fuzz_target> <duration_seconds> [seed_paths...] [--custom_mutator_path <path>] [--reset_output]
```

#### Parameters

##### fuzz_target (required, positional position 0)

**Type**: `str`

Fuzz target binary name (looked up in `/out_selective/<fuzz_target>`).

##### duration_seconds (required, positional position 1)

**Type**: `int`

Fuzzing duration in seconds.

##### seed_paths (optional, positional position 2+)

**Type**: `list` of strings

Optional seed file/dir paths.

##### --custom_mutator_path (optional, named parameter)

**Type**: `str`

Optional path to a custom mutator python script.

##### --reset_output (optional, flag)

**Type**: `bool` (default: `false`)

Reset output and start fresh.

## Requires Sandbox

fuzz

## Timeout

3600 seconds
