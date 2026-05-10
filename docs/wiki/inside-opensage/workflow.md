---
icon: lucide/workflow
---

# OpenSage Workflow

Every OpenSage run passes through the same lifecycle: parse input, build a session with its sandboxes, load the agent and its plugins, drive the reason-act loop, then tear down. Entry points (`opensage web`, evaluation runners, RL rollouts) differ in what wraps the session and what triggers execution, but environment preparation is shared.

## Lifecycle Phases

1. **Input parsing**: validate the config path, agent directory, and flags. Configure logging.
2. **Session creation**: assign a session ID, load and expand the TOML config, instantiate the session with its managers (`config`, `agents`, `sandboxes`, `neo4j`, `ensemble`).
3. **Sandbox preparation**: introspect the agent for sandbox dependencies, prune unused sandboxes, initialize shared volumes, launch containers, run per-sandbox initializers.
4. **Agent and plugin loading**: import `agent.py`, call `mk_agent(session_id=...)`, discover and instantiate plugins.
5. **Service wiring**: hook ADK session, artifact, memory, and credential services into the runtime (entry-point-specific).
6. **Execution**: drive the agent's reason-act loop over user input or benchmark samples.
7. **Cleanup**: snapshot or destroy sandboxes, clear the session registry, release Docker resources.

Phases 1 through 4 and phase 7 behave the same across `opensage web`, the evaluation runners, and the RL integrations. Phases 5 and 6 are where the entry points diverge: the web path stands up a FastAPI server and streams events to a browser; the evaluation path fans samples across workers; RL rollouts feed trajectories to an external trainer.

For the evaluation entry point, see [Evaluation Workflow](../developer-guide/evaluation/workflow.md). For the RL variants, see [RL Training Integration](../developer-guide/rl-integration/index.md). The rest of this page walks the `opensage web` path as a worked example.

## Example: `opensage web`

What happens between `uv run opensage web ...` and the first browser request.

### Step 1: Command Parsing and Validation

- The CLI parses command-line arguments.
- Validates that `config_path` exists and is a file.
- Validates that `agent_dir` exists and is a directory.
- Sets up logging based on `--log_level`.

### Step 2: Optional Neo4j Logging Setup

If `--neo4j_logging` is set:

- Imports `enable_neo4j_logging` from `opensage.features.agent_history_tracker`.
- Enables Neo4j logging via monkey patches so events stream into Neo4j for later analysis.

### Step 3: Environment Preparation (`_prepare_environment_async`)

The core setup phase. It creates the OpenSage session and initializes every resource the agent needs.

#### 3.1 Create Session ID

- Generates a unique UUID: `str(uuid.uuid4())`.
- Example: `"550e8400-e29b-41d4-a716-446655440000"`.

#### 3.2 Create Session

```python
import opensage

session = opensage.get_session(
    session_id=session_id,
    config_path=config_path,
)
```

- Loads the TOML configuration file.
- Expands template variables (for example `${VAR_NAME}`).
- Creates a session instance with all managers:
    - `config`: configuration manager.
    - `agents`: `DynamicAgentManager`.
    - `sandboxes`: `SandboxManager`.
    - `neo4j`: Neo4j client manager.
    - `ensemble`: ensemble manager.

#### 3.3 Load Agent and Collect Dependencies

```python
mk_agent = _load_mk_agent_from_dir(agent_dir)
dummy_agent = mk_agent(session_id=session_id)
sandbox_dependencies = collect_sandbox_dependencies(dummy_agent)
```

- Dynamically imports `agent.py` from the agent directory.
- Extracts the `mk_agent` function.
- Builds a dummy agent instance to inspect dependencies.
- Collects which sandbox types the agent requires.

#### 3.4 Prune Unused Sandboxes

- Compares required sandboxes against configured sandboxes.
- Removes sandbox configurations the agent does not need.
- Saves startup time by skipping unnecessary containers.

#### 3.5 Initialize Shared Volumes

```python
session.sandboxes.initialize_shared_volumes()
```

- Creates shared volumes for scripts and data.
- Configures volume mounts for all sandbox containers.

#### 3.6 Launch Sandbox Containers

```python
await session.sandboxes.launch_all_sandboxes()
```

For each required sandbox type:

- Reads sandbox configuration from `session.config.sandbox.sandboxes[sandbox_type]`.
- Creates a Docker container (or attaches to an existing one).
- Sets up network, volumes, and environment variables.
- Starts the container.
- Stores the sandbox instance in `session.sandboxes._sandboxes[sandbox_type]`.

#### 3.7 Initialize Sandboxes

```python
await session.sandboxes.initialize_all_sandboxes(continue_on_error=True)
```

For each sandbox:

- Selects the appropriate initializer.
- Calls `async_initialize()` (installs tools and dependencies, prepares resources).
- Marks the sandbox as ready.
- Continues even if one sandbox fails (`continue_on_error=True`).

### Step 4: Load Agent

```python
mk_agent = _load_mk_agent_from_dir(agent_dir)
root_agent = mk_agent(session_id=session_id)
```

- Imports the agent module again (picking up the latest code).
- Calls `mk_agent()` with the session ID.
- The agent constructor links to the session and configures tools and sub-agents.

### Step 5: Load Plugins

```python
enabled_plugins = session.config.plugins.enabled or []
plugins = load_plugins(
    enabled_plugins,
    agent_dir=agent_dir,
    adk_plugin_params=session.config.plugins.adk_plugin_params,
    extra_plugin_dirs=session.config.plugins.extra_plugin_dirs,
)
```

- Reads the plugin list from configuration.
- Discovers plugins from default, shared, and agent-local directories.
- Loads each plugin (ADK `.py` or Claude-Code hook `.json`) as an independent instance.

### Step 6: Create ADK Services

```python
session_service = InMemorySessionServiceBridge()
artifact_service = InMemoryArtifactService()
memory_service = InMemoryMemoryService()
credential_service = InMemoryCredentialService()
eval_sets_manager = LocalEvalSetsManager(agents_dir=agents_dir_parent)
eval_set_results_manager = LocalEvalSetResultsManager(agents_dir=agents_dir_parent)
```

- Creates in-memory services for ADK integration.
- The session service bridges ADK sessions with OpenSage sessions.

### Step 7: Determine App Name

```python
app_name = os.path.basename(os.path.dirname(agent_dir.rstrip(os.sep)))
```

- Uses the parent directory name as the app name.

### Step 8: Create Web Server

```python
web_server = WebServer(
    app_name=app_name,
    root_agent=root_agent,
    fixed_session_id=session_id,
    session_service=session_service,
    artifact_service=artifact_service,
    memory_service=memory_service,
    credential_service=credential_service,
    eval_sets_manager=eval_sets_manager,
    eval_set_results_manager=eval_set_results_manager,
    plugins=plugins,
)
```

- Configures FastAPI endpoints for agent execution, events, artifacts, and the UI.

### Step 9: Pre-create ADK Session

```python
await session_service.create_session(
    app_name=web_server.app_name,
    user_id="user",
    state={"opensage_session_id": session_id},
    session_id=session_id,
)
```

- Creates an ADK session that maps to the OpenSage session.

### Step 10: Create FastAPI App

```python
app = web_server.get_fast_api_app(allow_origins=None, enable_dev_ui=True)
```

### Step 11: Start Uvicorn Server

```python
config = uvicorn.Config(app, host=host, port=port, log_level=log_level.lower())
server = uvicorn.Server(config)
server.install_signal_handlers = lambda: None
server.run()
```

### User Interaction Flow

- The user opens a browser to `http://localhost:8000`.
- The dev UI loads and connects to the backend.
- The user sends a chat message.
- The backend runs the agent and streams events back to the UI.
- If a [`[fake_user]`](../reference/configuration/fake-user.md) callback is configured, the server automatically calls it after each invocation. When the callback returns a follow-up message, the server injects it as the next user message and continues streaming the agent's response within the same SSE connection. The loop repeats until the callback returns `None`. Fake-user messages are recorded in the session history and visible on page refresh, but are not displayed in real time in the UI.

### Cleanup

When the server stops (Ctrl+C):

- CLI-level signal handling interrupts `server.run()`.
- Cleanup or session-snapshot persistence runs in the `finally` block.
- Sandbox containers stop only when `auto_cleanup=True`.
- The session registry clears when cleanup runs.
