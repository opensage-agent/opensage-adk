---
name: general
description: "General-purpose tools for interacting with the sandbox environment and filesystem (reading files, inspecting paths, simple utilities). Available tools: read-file."
---

# General Tools

Category of tools for general interaction with the sandbox environment and filesystem.

## Available Tools

- **read-file**: Read a specific line range (with context) from a file path (returns JSON)

## Usage

Use these tools when you need to interact with the filesystem in a safe, structured way (e.g., inspect a file around a specific line) rather than running ad-hoc shell commands.

## Common Use Cases

- Reading a file with line context around a known line number
- Quickly inspecting a file on disk without dumping the whole content

## Requires Sandbox

main
