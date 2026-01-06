# sage-x web - Interactive Development Entry Point

The `sage-x web` command starts an interactive web UI for developing and debugging agents.

## CLI Commands

### sage-x web

Starts an interactive web UI for agent development and debugging.

```bash
uv run sage-x web \
  --config /path/to/config.toml \
  --agent /path/to/agent_dir \
  --port 8000 \
  --neo4j_logging  # optional
```

### sage-x dependency-check

Checks if external dependencies are properly installed.

```bash
uv run sage-x dependency-check
```

This command verifies:
- **CodeQL**: Required for CodeQL static analysis features
- **Docker**: Required for native Docker sandbox backend
- **kubectl**: Required for Kubernetes sandbox backend

All dependencies are optional unless you plan to use the corresponding features. The command will show:
- ✓ Green checkmarks for available dependencies
- ⚠ Yellow warnings for missing optional dependencies
- ✗ Red errors for missing required dependencies (if any)

**Example Output:**

```
Checking SAGE-X dependencies...

Checking CodeQL...
  ✓ CodeQL binary found at /path/to/codeql

Checking Docker...
  ✓ Docker daemon is running and accessible

Checking kubectl...
  ⚠ kubectl command not found in PATH. Install kubectl to use Kubernetes backend.
    Note: Only required when using Kubernetes sandbox backend

============================================================
⚠ Some dependencies missing (2/3 available)

Note: Missing dependencies are optional unless you plan to use
the corresponding features.
============================================================
```

## Step-by-Step Workflow

### Step 1: Command Parsing and Validation

1. CLI parses command-line arguments
2. Validates that `config_path` exists and is a file
3. Validates that `agent_dir` exists and is a directory
4. Sets up logging based on `--log_level` option

### Step 2: Optional Neo4j Logging Setup

If `--neo4j_logging` flag is provided:
1. Imports `enable_neo4j_logging` from `<package>.features.agent_history_tracker`
2. Enables Neo4j logging via monkey patches
3. This allows event logging to Neo4j for analysis

### Step 3: Environment Preparation (`_prepare_environment_async`)

This is the core setup phase that creates the SAGE-X session and initializes all resources.

#### 3.1 Create Session ID
- Generates a unique UUID for the session
- Format: `str(uuid.uuid4())`
- Example: `"550e8400-e29b-41d4-a716-446655440000"`

#### 3.2 Create AigiseSession
```python
aigise_session = get_aigise_session(
    aigise_session_id=session_id,
    config_path=config_path
)
```
- Loads TOML configuration file
- Expands template variables (e.g., `${VAR_NAME}`)
- Creates `AigiseSession` instance with all managers:
  - `config`: Configuration manager
  - `agents`: DynamicAgentManager
  - `sandboxes`: AigiseSandboxManager
  - `neo4j`: AigiseNeo4jClientManager
  - `ensemble`: AigiseEnsembleManager

#### 3.3 Load Agent and Collect Dependencies
```python
mk_agent = _load_mk_agent_from_dir(agent_dir)
dummy_agent = mk_agent(aigise_session_id=session_id)
sandbox_dependencies = collect_sandbox_dependencies(dummy_agent)
```
- Dynamically imports `agent.py` from the agent directory
- Extracts the `mk_agent` function
- Creates a dummy agent instance to analyze dependencies
- Collects which sandbox types the agent requires (e.g., `{"main", "joern", "neo4j"}`)

#### 3.4 Prune Unused Sandboxes
- Compares required sandboxes with configured sandboxes
- Removes sandbox configurations that are not needed
- Example: If agent only needs `{"main", "joern"}`, removes `gdb_mcp`, `pdb_mcp`, etc.
- This optimizes startup time by not launching unnecessary containers

#### 3.5 Initialize Shared Volumes
```python
aigise_session.sandboxes.initialize_shared_volumes()
```
- Creates Docker volumes for shared data:
  - `scripts_volume`: Read-only scripts and tools
  - `data_volume`: Read-write data directory
- Configures volume mounts for all sandbox containers
- Volumes are shared across all sandboxes in the session

#### 3.6 Launch Sandbox Containers
```python
await aigise_session.sandboxes.launch_all_sandboxes()
```
For each required sandbox type:
1. Gets sandbox configuration from `session.config.sandbox.sandboxes[sandbox_type]`
2. Creates Docker container (or connects to existing one)
3. Sets up network, volumes, environment variables
4. Starts the container
5. Stores sandbox instance in `session.sandboxes._sandboxes[sandbox_type]`

#### 3.7 Initialize Sandboxes
```python
await aigise_session.sandboxes.initialize_all_sandboxes(continue_on_error=True)
```
For each sandbox:
1. Finds the appropriate initializer (e.g., `JoernInitializer`, `FuzzInitializer`)
2. Calls `async_initialize()` method:
   - Installs tools and dependencies
   - Sets up environment
   - Prepares resources
   - Example: Joern initializer runs CPG generation scripts
3. Marks sandbox as ready
4. Continues even if one sandbox fails (due to `continue_on_error=True`)

### Step 4: Load Agent

```python
mk_agent = _load_mk_agent_from_dir(agent_dir)
root_agent = mk_agent(aigise_session_id=session_id)
```

1. Imports agent module again (ensuring latest code)
2. Calls `mk_agent()` with the session ID
3. Agent constructor:
   - Gets session via `get_aigise_session(session_id)`
   - Loads dynamic tools from filesystem (if using `AigiseAgent`)
   - Sets up tool combos, sub-agents, etc.
4. Returns configured agent instance

### Step 5: Load Plugins

```python
enabled_plugins = aigise_session.config.plugins.enabled or []
plugins = load_plugins(enabled_plugins)
```

1. Reads plugin list from configuration
2. Loads plugin classes dynamically
3. Instantiates plugin objects
4. Returns list of plugin instances

### Step 6: Create ADK Services

```python
session_service = AigiseInMemorySessionService()
artifact_service = InMemoryArtifactService()
memory_service = InMemoryMemoryService()
credential_service = InMemoryCredentialService()
eval_sets_manager = LocalEvalSetsManager(agents_dir=agents_dir_parent)
eval_set_results_manager = LocalEvalSetResultsManager(agents_dir=agents_dir_parent)
```

- Creates in-memory services for ADK integration
- `AigiseInMemorySessionService` bridges ADK sessions with SAGE-X sessions
- Other services are standard ADK in-memory implementations

### Step 7: Determine App Name

```python
app_name = os.path.basename(os.path.dirname(agent_dir.rstrip(os.sep)))
```

- Extracts parent directory name as app name
- Example: `/path/to/examples/agents/my_agent` → `app_name = "agents"`

### Step 8: Create Web Server

```python
web_server = AigiseWebServer(
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

- Creates `AigiseWebServer` instance
- Configures all services and agent
- Sets up FastAPI endpoints for:
  - Agent execution (`/run`)
  - Server-Sent Events (`/events`)
  - Live streaming (`/live`)
  - Session management
  - Artifact access
  - Dev UI static files

### Step 9: Pre-create ADK Session

```python
await session_service.create_session(
    app_name=web_server.app_name,
    user_id="user",
    state={"aigise_session_id": session_id},
    session_id=session_id,
)
```

- Creates ADK session that maps to SAGE-X session
- Stores `aigise_session_id` in session state
- This allows ADK Runner to find the SAGE-X session

### Step 10: Create FastAPI App

```python
app = web_server.get_fast_api_app(allow_origins=None, enable_dev_ui=True)
```

- Generates FastAPI application with all routes
- Enables CORS middleware
- Mounts static files for Dev UI
- Sets up WebSocket endpoints

### Step 11: Start Uvicorn Server

```python
config = uvicorn.Config(app, host=host, port=port, reload=reload, log_level=log_level.lower())
server = uvicorn.Server(config)
server.run()
```

- Configures Uvicorn ASGI server
- Starts server on specified host/port
- If `reload=True`, watches for code changes and restarts
- Server runs until interrupted (Ctrl+C)

## User Interaction Flow

Once the server is running:

1. User opens browser to `http://localhost:8000`
2. Dev UI loads and connects to backend
3. User types a message in the chat interface
4. Frontend sends POST request to `/run` endpoint
5. Web server creates ADK Runner with the agent
6. Runner executes agent with user message
7. Events are streamed back via Server-Sent Events
8. Frontend displays agent responses in real-time
9. User can continue conversation or start new session

## Key Differences from Evaluation

- **Single session**: One SAGE-X session for the entire web server
- **Interactive**: User can send multiple messages
- **Real-time**: Events streamed as they happen
- **Development-focused**: Hot reload, debugging tools, Dev UI
- **Long-lived**: Server runs until manually stopped

## Cleanup

When server stops (Ctrl+C):
1. Signal handler calls `cleanup_all_sessions()`
2. All sandbox containers are stopped
3. Shared volumes are cleaned up (if configured)
4. Session registry is cleared

## Related Topics

- [Core Concepts](Core-Concepts.md) - Understanding sessions and sandboxes
- [Development Guides](Development-Guides.md) - How to develop agents
- [Testing Debugging](Testing-Debugging.md) - Debugging with web UI
