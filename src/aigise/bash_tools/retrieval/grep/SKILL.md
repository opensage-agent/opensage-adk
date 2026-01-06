---
name: grep
description: Search the codebase inside the running container for a given regex pattern.
should_run_in_sandbox: main
returns_json: false
---

# Grep Tool

Search the codebase inside the running container for a given regex pattern.
The pattern is passed to grep with flags '-rnE' for recursive, line-numbered,
extended-regex searches.

## Usage

```bash
scripts/grep.sh "pattern"
```

## Parameters

- `pattern`: A regex pattern to search for.

## Return Value

Returns text output in grep format: `file_path:line_number:matched_line`.
Each match is on a separate line. Limited to 150 matches.

## Requires Sandbox

main
