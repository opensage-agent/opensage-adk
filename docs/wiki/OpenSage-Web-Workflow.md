# opensage web - workflow details

This page contains the detailed workflow for the `opensage web` entry point.

## Step-by-Step Workflow

### Step 1: Command Parsing and Validation

- CLI parses command-line arguments
- Validates that `config_path` exists and is a file
- Validates that `agent_dir` exists and is a directory
- Sets up logging based on `--log_level` option

### Step 2: Optional Neo4j Logging Setup

If `--neo4j_logging` flag is provided:

- Imports `enable_neo4j_logging` from `opensage.features.agent_history_tracker`
- Enables Neo4j logging via monkey patches
- This allows event logging to Neo4j for analysis

### Step 3: Environment Preparation (`_prepare_environment_async`)

This is the core setup phase that creates the OpenSage session and initializes
all resources.

#### 3.1 Create Session ID

- Generates a unique UUID for the session
- Format: `str(uuid.uuid4())`
- Example: `"550e8400-e29b-41d4-a716-446655440000"`

#### 3.2 Create Session

```python
import opensage

session = opensage.get_session(
    session_id=session_id,
    config_path=config_path
)
```

- Loads TOML configuration file
- Expands template variables (e.g., `${VAR_NAME}`)
- Creates a session instance with all managers:
  - `config`: Configuration manager
  - `agents`: DynamicAgentManager
  - `sandboxes`: SandboxManager
  - `neo4j`: Neo4j client manager
  - `ensemble`: Ensemble manager

#### 3.3 Load Agent and Collect Dependencies

```python
mk_agent = _load_mk_agent_from_dir(agent_dir)
dummy_agent = mk_agent(session_id=session_id)
sandbox_dependencies = collect_sandbox_dependencies(dummy_agent)
```

- Dynamically imports `agent.py` from the agent directory
- Extracts the `mk_agent` function
- Creates a dummy agent instance to analyze dependencies
- Collects which sandbox types the agent requires

#### 3.4 Prune Unused Sandboxes

- Compares required sandboxes with configured sandboxes
- Removes sandbox configurations that are not needed
- Optimizes startup time by not launching unnecessary containers

#### 3.5 Initialize Shared Volumes

```python
session.sandboxes.initialize_shared_volumes()
```

- Creates shared volumes for scripts and data
- Configures volume mounts for all sandbox containers

#### 3.6 Launch Sandbox Containers

```python
await session.sandboxes.launch_all_sandboxes()
```

For each required sandbox type:

- Gets sandbox configuration from `session.config.sandbox.sandboxes[sandbox_type]`
- Creates Docker container (or connects to existing one)
- Sets up network, volumes, environment variables
- Starts the container
- Stores sandbox instance in `session.sandboxes._sandboxes[sandbox_type]`

#### 3.7 Initialize Sandboxes

```python
await session.sandboxes.initialize_all_sandboxes(continue_on_error=True)
```

For each sandbox:

- Finds the appropriate initializer
- Calls `async_initialize()` (installs tools/deps, prepares resources)
- Marks sandbox as ready
- Continues even if one sandbox fails (`continue_on_error=True`)

### Step 4: Load Agent

```python
mk_agent = _load_mk_agent_from_dir(agent_dir)
root_agent = mk_agent(session_id=session_id)
```

- Imports agent module again (ensuring latest code)
- Calls `mk_agent()` with the session ID
- Agent constructor links to the session and configures tools/sub-agents

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

- Reads plugin list from configuration
- Discovers plugins from default, shared, and agent-local directories
- Loads each plugin (ADK `.py` or CC hook `.json`) as an independent instance

### Step 6: Create ADK Services

```python
session_service = InMemorySessionServiceBridge()
artifact_service = InMemoryArtifactService()
memory_service = InMemoryMemoryService()
credential_service = InMemoryCredentialService()
eval_sets_manager = LocalEvalSetsManager(agents_dir=agents_dir_parent)
eval_set_results_manager = LocalEvalSetResultsManager(agents_dir=agents_dir_parent)
```

- Creates in-memory services for ADK integration
- Session service bridges ADK sessions with OpenSage sessions

### Step 7: Determine App Name

```python
app_name = os.path.basename(os.path.dirname(agent_dir.rstrip(os.sep)))
```

- Extracts parent directory name as app name

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

- Configures FastAPI endpoints for agent execution, events, artifacts, and UI

### Step 9: Pre-create ADK Session

```python
await session_service.create_session(
    app_name=web_server.app_name,
    user_id="user",
    state={"opensage_session_id": session_id},
    session_id=session_id,
)
```

- Creates an ADK session that maps to the OpenSage session

### Step 10: Create FastAPI App

```python
app = web_server.get_fast_api_app(allow_origins=None, enable_dev_ui=True)
```

### Step 11: Start Uvicorn Server

```python
config = uvicorn.Config(app, host=host, port=port, reload=reload, log_level=log_level.lower())
server = uvicorn.Server(config)
server.run()
```

## User Interaction Flow

- User opens browser to `http://localhost:8000`
- Dev UI loads and connects to backend
- User sends a chat message
- Backend runs the agent and streams events back to the UI

## Cleanup

When server stops (Ctrl+C):

- Signal handler calls `cleanup_all_sessions()`
- Sandbox containers are stopped
- Shared volumes are cleaned up (if configured)
- Session registry is cleared
