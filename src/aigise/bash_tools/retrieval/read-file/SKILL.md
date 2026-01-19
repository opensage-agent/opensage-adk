---
name: read-file
description: Tool to get a specific line and surrounding lines from a file.
should_run_in_sandbox: main
returns_json: true

---

# Read File Tool

Tool to get a specific line and surrounding lines from a file.

## Usage

```bash
python3 /bash_tools/retrieval/read-file/scripts/read_file.py --file "/path/to/file" --linenum 10 --context 5
```

## Parameters

- `file`: The path to the file. This should be an absolute path.
- `linenum`: The line number to retrieve.
- `context`: The number of lines of context to include before and after the specified line. DO NOT set this more than 100.

## Requires Sandbox

main
