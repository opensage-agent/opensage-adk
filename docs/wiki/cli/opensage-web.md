# opensage web

> Full flag reference: [`opensage web --help`](../generated/cli/opensage-web.md)

The `opensage web` command starts an interactive web UI for developing and debugging agents.

## Usage

```bash
uv run opensage web \
  --config /path/to/config.toml \
  --agent /path/to/agent_dir \
  --port 8000 \
  --neo4j_logging  # optional
```

## Session persistence and resume

By default, `opensage web` keeps sandbox snapshots on exit so sessions can be
resumed later.

```bash
# Explicitly control cleanup behavior
uv run opensage web --config /path/to/config.toml --agent /path/to/agent_dir --auto_cleanup true

# Resume latest snapshot
uv run opensage web --resume

# Resume a specific saved snapshot
uv run opensage web --resume-from ctf_agent_c0606edc-2fff-496d-8964-48bdd7f0bd23
```

Snapshots are stored under:
`~/.local/opensage/sessions/<agent_name>_<session_id>/`

- `--resume` restores the latest saved snapshot.
- `--resume-from` restores a specific saved snapshot by directory name, bare
  session ID suffix, or absolute path.

---

## Internal workflow

The sections below describe what happens under the hood when `opensage web`
runs. This is useful for debugging and understanding the system internals.

### 1. Command parsing and validation

- Validates that `config_path` exists and is a file
- Validates that `agent_dir` exists and is a directory
- Sets up logging based on `--log_level`
- If `--neo4j_logging` is set, enables Neo4j event logging via
  `opensage.features.agent_history_tracker`

### 2. Environment preparation

Core setup phase (`_prepare_environment_async`) that creates the session and
initializes all resources:

**Create session**

```python
import opensage

session = opensage.get_session(
    session_id=session_id,   # generated UUID
    config_path=config_path
)
```

The session loads the TOML config (expanding `${VAR_NAME}` template variables)
and creates managers for configuration, agents, sandboxes, Neo4j, and ensembles.

**Load agent and collect sandbox dependencies**

```python
mk_agent = _load_mk_agent_from_dir(agent_dir)
dummy_agent = mk_agent(session_id=session_id)
sandbox_dependencies = collect_sandbox_dependencies(dummy_agent)
```

A dummy agent instance is created to determine which sandbox types are needed.
Unused sandbox configurations are pruned to speed up startup.

**Launch sandboxes**

```python
session.sandboxes.initialize_shared_volumes()
await session.sandboxes.launch_all_sandboxes()
await session.sandboxes.initialize_all_sandboxes(continue_on_error=True)
```

For each required sandbox: creates the Docker container, sets up networking and
volumes, starts the container, and runs initialization (tool/dependency
installation). Initialization continues even if one sandbox fails.

### 3. Load agent and plugins

```python
root_agent = mk_agent(session_id=session_id)

plugins = load_plugins(
    enabled_plugins,
    agent_dir=agent_dir,
    adk_plugin_params=session.config.plugins.adk_plugin_params,
    extra_plugin_dirs=session.config.plugins.extra_plugin_dirs,
)
```

The agent module is re-imported to pick up latest code. Plugins are discovered
from default, shared, and agent-local directories and loaded as ADK `.py` or CC
hook `.json` instances.

### 4. Wire up ADK services and web server

```python
session_service = InMemorySessionServiceBridge()
artifact_service = InMemoryArtifactService()
memory_service = InMemoryMemoryService()
credential_service = InMemoryCredentialService()

web_server = WebServer(
    app_name=app_name,
    root_agent=root_agent,
    fixed_session_id=session_id,
    session_service=session_service,
    artifact_service=artifact_service,
    memory_service=memory_service,
    credential_service=credential_service,
    plugins=plugins,
    ...
)
```

In-memory services bridge ADK sessions with OpenSage sessions. The app name is
derived from the agent directory's parent folder.

### 5. Start server

```python
app = web_server.get_fast_api_app(allow_origins=None, enable_dev_ui=True)
config = uvicorn.Config(app, host=host, port=port, reload=reload, log_level=log_level.lower())
server = uvicorn.Server(config)
server.run()
```

An ADK session is pre-created mapping to the OpenSage session, then Uvicorn
serves the FastAPI app.

### 6. User interaction flow

1. User opens `http://localhost:8000` in a browser
2. Dev UI loads and connects to the backend
3. User sends a chat message
4. Backend runs the agent and streams events back to the UI

### 7. Cleanup

When the server stops (Ctrl+C):

- Signal handler calls `cleanup_all_sessions()`
- Sandbox containers are stopped
- Shared volumes are cleaned up (if configured)
- Session registry is cleared
