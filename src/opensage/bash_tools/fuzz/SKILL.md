---
name: fuzz
description: "Fuzzing tools for automated bug discovery using AFL++. Runs fuzzing campaigns, extracts crash inputs, performs selective instrumentation with AFL_LLVM_ALLOWLIST, and supports simplified Python fuzzing. Use when the user asks about fuzz testing, AFL++, crash analysis, automated vulnerability discovery, security testing a binary, running a fuzzer, or extracting crash reproducers. Available tools: run-fuzzing-campaign, extract-crashes, simplified-python-fuzzer, selective-instrument."
---

# Fuzzing Tools

Automated fuzzing and bug discovery using AFL++ and Python-based fuzzers. Supports full fuzzing campaigns, crash extraction, and targeted instrumentation.

## Available Tools

| Tool | Purpose | When to use |
|------|---------|-------------|
| **run-fuzzing-campaign** | Run AFL++ fuzzing campaigns | Starting a new fuzzing run with configurable duration, seeds, and custom mutators |
| **extract-crashes** | Extract crash inputs from fuzzer output | After a fuzzing campaign finishes, to collect and deduplicate crash-triggering inputs |
| **selective-instrument** | Recompile with AFL_LLVM_ALLOWLIST | Focusing fuzzing on specific functions or code regions for directed testing |
| **simplified-python-fuzzer** | Run a Python fuzzer script | Quick fuzz testing of Python code with a fixed duration |

## Workflow

1. **Instrument** (optional): Use `selective-instrument` to write an allowlist and recompile the target for focused fuzzing
2. **Fuzz**: Run `run-fuzzing-campaign` with seed inputs, duration, and optional custom mutators
3. **Extract crashes**: Use `extract-crashes` to collect crash-triggering inputs from the fuzzer output directory
4. **Analyze**: Feed crash inputs back into the target binary to reproduce and triage bugs

## Requires Sandbox

fuzz
