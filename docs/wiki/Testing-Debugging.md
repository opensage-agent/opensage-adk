# Testing & Debugging

## Running Tests

```bash
# Run all tests
uv run pytest tests/

# Run specific test file
uv run pytest tests/unit/test_session.py

# Run with coverage
uv run pytest --cov=src/aigise tests/
```

## Debugging with Web UI

The web UI is the primary debugging tool:

```bash
uv run aigise web \
  --config /path/to/config.toml \
  --agent /path/to/agent_dir \
  --port 8080 \
  --neo4j_logging  # optional
```

## Debugging Sandboxes

```python
# In your code
sandbox = session.sandboxes.get_sandbox("main")
result, exit_code = sandbox.run_command_in_container("pwd")
print(f"Working dir: {result}")
```

## Logging

SAGE-X uses structured logging:

```python
import logging
logger = logging.getLogger(__name__)
logger.info("Operation started", extra={"session_id": session_id})
```

## Common Debugging Tasks

- Check session logs
- Verify sandbox container status
- Check configuration values
- Verify tool imports
- Check ADK compatibility

## See Also

- [Troubleshooting](Troubleshooting.md) - Common issues and solutions
- [Best Practices](Best-Practices.md) - Development best practices
