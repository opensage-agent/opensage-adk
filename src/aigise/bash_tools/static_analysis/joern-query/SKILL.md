---
name: joern-query
description: Tool to run a custom Joern query against the code property graph (you may need to importCpg("/cpg.bin") first).
should_run_in_sandbox: joern
returns_json: false

---

# Joern Query Tool

Tool to run a custom Joern query against the code property graph.

## Usage

```bash
python3 scripts/joern_query.py "query_string"
```

If you see `No projects loaded`, import the CPG first:

```bash
python3 scripts/joern_query.py 'importCpg("/cpg.bin")'
```

## Parameters

- `query`: The Joern query string to execute.

## Return Value

Returns plain text output with the raw response from the Joern client.

## Requires Sandbox

joern
