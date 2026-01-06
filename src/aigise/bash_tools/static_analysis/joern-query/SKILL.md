---
name: joern-query
description: Tool to run a custom Joern query against the code property graph.
should_run_in_sandbox: joern
returns_json: false

---

# Joern Query Tool

Tool to run a custom Joern query against the code property graph.

## Usage

```bash
python3 scripts/joern_query.py "query_string"
```

## Parameters

- `query`: The Joern query string to execute.

## Return Value

Returns plain text output with the raw response from the Joern client.

## Requires Sandbox

joern
