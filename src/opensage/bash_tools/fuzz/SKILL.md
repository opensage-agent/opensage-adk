---
name: fuzz
description: "Fuzzing tools for automated bug discovery. These tools support running fuzzing campaigns with AFL++, extracting crash information, and simplified Python fuzzing workflows. Available tools: run-fuzzing-campaign, extract-crashes, simplified-python-fuzzer."
---

# Fuzzing Tools

Category of tools for automated fuzzing and bug discovery using AFL++ and other fuzzing frameworks. These tools help set up fuzzing campaigns, analyze results, and extract useful information from fuzzing runs.

## Available Tools

- **run-fuzzing-campaign**: Run a complete AFL++ fuzzing campaign with configurable duration, seeds, and custom mutators
- **extract-crashes**: Extract and analyze crash information from fuzzing results
- **simplified-python-fuzzer**: Simplified Python-based fuzzing tool for quick testing
- **selective-instrument**: Recompile with AFL++ selective instrumentation (AFL_LLVM_ALLOWLIST) and run focused fuzzing on specific functions/files

## Usage

These tools work with fuzz and main sandbox types, depending on the specific tool.

## Common Use Cases

- Running AFL++ fuzzing campaigns on target binaries
- Analyzing fuzzing results and crash information
- Monitoring fuzzing progress and statistics
- Using custom mutators for domain-specific fuzzing
- Extracting and categorizing discovered crashes

## Requires Sandbox

fuzz
