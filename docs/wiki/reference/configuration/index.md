---
icon: lucide/table-properties
---

# Configuration Reference

Field-level reference for every section of `config.toml`. Each linked page lists every supported field with its type, description, and default value.

For how to use these fields (narrative, examples, progression), see the [Customize Agents guide](../../get-started/customize/index.md) under *Get Started*.

## File Format and Loading Order

Configuration files use TOML. The loader applies them in this order:

1. **Default configuration**: `src/opensage/templates/configs/default_config.toml` (used when no config is specified).
2. **Custom configuration**: the path passed as `config_path` when creating a session.

The config system supports template variables (`${VAR_NAME}`), nested sections, environment-variable references, and automatic conversion into typed Python dataclasses.

## Top-Level (Root) Fields

Fields defined at the top level of the TOML file, outside any `[section]`.

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `task_name` | `string` | Name identifier for the current task/session | `None` |
| `src_dir_in_sandbox` | `string` | Path to source code directory within sandbox containers | `"/shared/code"` |
| `agent_storage_path` | `string` | Path where dynamically created agents are stored | `None` |
| `default_host` | `string` | Default hostname for services (Neo4j, MCP) | `None` → `127.0.0.1` |
| `auto_cleanup` | `boolean` | Clean up resources when the session ends | `true` |

## Template Variables

Define reusable values as top-level UPPERCASE keys, then reference them anywhere with `${VAR_NAME}`.

**Rules:**

1. Only top-level UPPERCASE keys become template variables.
2. References work in any field, in any section.
3. Variables expand recursively across the file.
4. An undefined variable is a load-time error.

**Example:**

```toml
# Top-level template variables
DEFAULT_IMAGE = "ubuntu:20.04"
MAIN_MODEL = "openai/gpt-4"
NEO4J_PASSWORD = "mypassword123"

# Referenced further down
[sandbox.sandboxes.main]
image = "${DEFAULT_IMAGE}"

[llm.model_configs.main]
model_name = "${MAIN_MODEL}"

[neo4j]
password = "${NEO4J_PASSWORD}"
```

## Sections

- [`[llm]`](./llm.md): model profiles and sampling parameters.
- [`[sandbox]`](./sandbox.md): execution environments, per-sandbox container and build fields.
- [`[mcp]`](./mcp.md): MCP service definitions.
- [`[history]`](./history.md): tool-response handling and event compaction.
- [`[plugins]`](./plugins.md): enabled plugins and discovery paths.
- [`[fake_user]`](./fake-user.md): automated user-simulator callback for multi-turn interactions.
- [`[agent_ensemble]`](./agent-ensemble.md): multi-model ensemble fields.
- [`[neo4j]`](./neo4j.md): graph database connection.
- [`[build]`](./build.md): build and run commands for target artifacts.

## Sandbox Images and Python Dependencies

A few sandboxes require Python tooling inside their Docker images. In the default configuration template (`src/opensage/templates/configs/default_config.toml`):

- **`sandbox.sandboxes.main`**
    - Built from `src/opensage/templates/dockerfiles/main/Dockerfile`.
    - Provides `python3` at `/app/.venv/bin/python`.
    - Installs the Python package `neo4j` (used by `src/opensage/sandbox/initializers/main.py`).

- **`sandbox.sandboxes.joern`**
    - Built from `src/opensage/templates/dockerfiles/joern/Dockerfile`.
    - Provides `python3` at `/app/.venv/bin/python`.
    - Installs the Python packages `httpx` and `websockets` (used by Joern query helper scripts).

These images install Python dependencies with `uv` at Docker-build time (create `/app/.venv`, then run `uv pip install ...`), not at runtime inside a running container.

## Dynamic Host Resolution

When `default_host` is unset, services like Neo4j and MCP fall back to `127.0.0.1`. Set `default_host` at the root level for Kubernetes deployments or when services run on another host.

## Troubleshooting

**`Template variable 'VAR_NAME' not found`**

- The variable must be defined as an UPPERCASE top-level key.
- The name is case-sensitive; it must match exactly.
- Check for typos in `${VAR_NAME}` references.

**Configuration not loading**

- Confirm the TOML syntax is valid.
- Use an absolute path if the relative path does not resolve.
- Read the load error: missing required fields are named in the message.
