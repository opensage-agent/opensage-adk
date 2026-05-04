---
icon: lucide/bug-off
---

# Troubleshooting

## Running Tests

OpenSage-ADK uses `pytest` under `uv`:

```bash
# Run all tests
uv run pytest tests/

# Run a specific test file
uv run pytest tests/unit/test_session.py

# Run with coverage
uv run pytest --cov=src/opensage tests/
```

## Debugging with the Web UI

The web UI is the primary debugging tool. Start it with the agent and config you want to inspect, and (optionally) enable Neo4j logging to capture every event for later analysis:

```bash
uv run opensage web \
  --config /path/to/config.toml \
  --agent /path/to/agent_dir \
  --port 8080 \
  --neo4j_logging  # optional
```

For the full flag reference, see [WebUI Reference](../reference/opensage-web.md).

## Debugging Sandboxes

You can drop into the sandbox layer from any session to run shell commands directly inside a container:

```python
sandbox = session.sandboxes.get_sandbox("main")
result, exit_code = sandbox.run_command_in_container("pwd")
print(f"Working dir: {result}")
```

This is useful for inspecting state when a tool fails: file presence, environment variables, installed dependencies.

## Logging

OpenSage uses structured logging. Attach contextual fields via the `extra` argument so log search by session ID stays cheap:

```python
import logging

logger = logging.getLogger(__name__)
logger.info("Operation started", extra={"session_id": session_id})
```


## Common Issues

### Provider Credentials

When using provider-backed models (for example `openai/...` or `anthropic/...`), make sure provider credentials are available in environment variables before runtime: `OPENAI_API_KEY` for OpenAI, `ANTHROPIC_API_KEY` for Anthropic, and so on.

### Template Variable Not Found

If you see `KeyError: Template variable 'VAR_NAME' not found`:

- Ensure the variable is defined as an UPPERCASE top-level variable in your TOML config.
- Check that the variable name matches exactly (case-sensitive).
- Verify there are no typos in `${VAR_NAME}` references.

### Configuration Not Loading

- Verify the TOML file syntax is correct (a TOML linter can help).
- Check the file path is correct; use absolute paths if relative paths do not resolve.
- Ensure all required fields are present (check error messages for hints).

### Dynamic Host Resolution

If `default_host` is not set, services like Neo4j and MCP will default to `127.0.0.1`. Set `default_host` at the root level of your TOML config for Kubernetes deployments or remote services.

### Still Having Issues?

Please report issues on the [OpenSage-ADK GitHub repository](https://github.com/opensage-agent/opensage-adk/issues).


## See Also

- [Best Practices](best-practices.md): development conventions.
- [Evaluation Workflow](evaluation/workflow.md): per-sample lifecycle for evaluations.
