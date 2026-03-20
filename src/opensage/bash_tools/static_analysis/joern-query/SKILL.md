---
name: joern-query
description: Run a Joern query.
should_run_in_sandbox: joern
returns_json: false

---

# Joern Query Tool

Tool to run a custom Joern query against the code property graph.

## Usage

```bash
python3 /bash_tools/static_analysis/joern-query/scripts/joern_query.py "query_string"
```

Tip: if needed, load the CPG first: `importCpg("/cpg.bin")`.

## Parameters

### query (required, positional position 0)

**Type**: `str`

Joern query string to execute.

## Return Value

Returns plain text output with the raw response from the Joern client.

## Requires Sandbox

joern
