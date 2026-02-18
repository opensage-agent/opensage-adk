# Configuration Guide

This document describes the OpenSage configuration system, including all configuration fields, their purposes, and how to write configuration files.

## Overview

OpenSage uses TOML (Tom's Obvious, Minimal Language) format for configuration files. The configuration system supports:

- **Template Variables**: Use `${VAR_NAME}` syntax for reusable values
- **Nested Sections**: Organize related settings into logical groups
- **Environment Variable Support**: Template variables can reference environment variables
- **Type Safety**: Automatic conversion to Python dataclasses with type checking

## Configuration File Location

Configuration files are loaded in the following order:

1. **Default Configuration**: `src/opensage/templates/configs/default_config.toml` (used when no config is specified)
2. **Custom Configuration**: Path specified via `config_path` parameter when creating a session

## Configuration Structure

The configuration is organized into several main sections:

```toml
# Top-level template variables (optional)
VARIABLE_NAME = "value"

# Root-level fields
task_name = "my_task"
src_dir_in_sandbox = "/shared/code"
default_host = "127.0.0.1"
auto_cleanup = true

# Section-based configuration
[neo4j]
# Neo4j database configuration

[sandbox]
# Sandbox configuration

[llm]
# LLM model configuration

[history]
# History and tool response configuration

[plugins]
# Plugin configuration

[agent_ensemble]
# Agent ensemble configuration

[build]
# Build and execution configuration

[mcp]
# Model Context Protocol services configuration
```

## Template Variables

OpenSage supports template variable expansion using `${VAR_NAME}` syntax.

### Rules:

1. **Top-level UPPERCASE variables** automatically become template variables
2. Variables can be referenced anywhere using `${VAR_NAME}`
3. Variables are expanded recursively throughout the configuration
4. Undefined variables cause an error at load time

### Example:

```toml
# Define template variables (UPPERCASE)
DEFAULT_IMAGE = "ubuntu:20.04"
MAIN_MODEL = "openai/gpt-4"
NEO4J_PASSWORD = "mypassword123"

# Use template variables
[sandbox.sandboxes.main]
image = "${DEFAULT_IMAGE}"

[llm.model_configs.main]
model_name = "${MAIN_MODEL}"

[neo4j]
password = "${NEO4J_PASSWORD}"
```

## Configuration Sections

### Root-Level Fields

These fields are defined at the top level of the configuration file:

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `task_name` | `string` | Name identifier for the current task/session | `None` |
| `src_dir_in_sandbox` | `string` | Path to source code directory within sandbox containers | `"/shared/code"` |
| `agent_storage_path` | `string` | Path where dynamically created agents are stored | `None` |
| `default_host` | `string` | Default hostname for services (used by Neo4j and MCP services) | `None` (falls back to `127.0.0.1`) |
| `auto_cleanup` | `boolean` | Whether to automatically cleanup resources when session ends | `true` |

**Example:**

```toml
task_name = "vulnerability_analysis"
src_dir_in_sandbox = "/shared/code"
agent_storage_path = "/tmp/agents"
default_host = "localhost"
auto_cleanup = true
```

### Neo4j Configuration

Configures the Neo4j graph database connection.

**Section:** `[neo4j]`

## Sandbox Images & Requirements (Practical Notes)

Some sandboxes require Python tooling inside their Docker images. In the default
configuration template (`src/opensage/templates/configs/default_config.toml`):

- **`sandbox.sandboxes.main`**
  - Built from `src/opensage/templates/dockerfiles/main/Dockerfile`
  - Provides `python3` via `/app/.venv/bin/python`
  - Installs Python package `neo4j` (used by `src/opensage/sandbox/initializers/main.py`)

- **`sandbox.sandboxes.joern`**
  - Built from `src/opensage/templates/dockerfiles/joern/Dockerfile`
  - Provides `python3` via `/app/.venv/bin/python`
  - Installs Python packages `httpx` and `websockets` (used by Joern query helper scripts)

These images install Python deps using `uv` in the Dockerfile (create `/app/.venv`
and run `uv pip install ...`), rather than at runtime inside a running container.

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `user` | `string` | Neo4j username | `None` |
| `password` | `string` | Neo4j password | `None` |
| `bolt_port` | `integer` | Neo4j Bolt protocol port | `7687` |
| `neo4j_http_port` | `integer` | Neo4j HTTP port | `7474` |

**Note:** The `uri` property is dynamically constructed as `neo4j://{default_host}:{bolt_port}`. If `default_host` is not set, it defaults to `127.0.0.1`.

**Example:**

```toml
[neo4j]
user = "neo4j"
password = "callgraphn4j!"
bolt_port = 7687
neo4j_http_port = 7474
```

### Sandbox Configuration

Configures sandbox environments (Docker containers or Kubernetes pods).

**Section:** `[sandbox]`

#### Top-Level Sandbox Settings

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `default_image` | `string` | Default Docker image for sandboxes | `None` |
| `backend` | `string` | Sandbox backend type: `"native"` (Docker) or `"k8s"` (Kubernetes; under development) | `"native"` |
| `project_relative_shared_data_path` | `string` | Path relative to project root for shared data (will be mounted as `/shared` in containers) | `None` |
| `absolute_shared_data_path` | `string` | Absolute path for shared data | `None` |
| `tolerations` | `list[dict]` | Kubernetes tolerations applied to all pods (k8s; under development) | `None` |

#### Per-Sandbox Configuration

Each sandbox type is configured under `[sandbox.sandboxes.<sandbox_type>]`:

**Common Sandbox Types:**
- `main`: Primary analysis sandbox
- `joern`: Joern static analysis sandbox
- `codeql`: CodeQL analysis sandbox
- `neo4j`: Neo4j database container
- `gdb_mcp`: GDB debugger MCP service
- `pdb_mcp`: PDB debugger MCP service
- `fuzz`: Fuzzing environment

**Container Configuration Fields:**

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `image` | `string` | Docker image name/tag | `None` |
| `container_id` | `string` | Connect to existing container (instead of creating new) | `None` |
| `timeout` | `integer` | Container operation timeout in seconds | `300` |
| `project_relative_dockerfile_path` | `string` | Path to Dockerfile relative to project root | `None` |
| `absolute_dockerfile_path` | `string` | Absolute path to Dockerfile | `None` |
| `command` | `string` | Override container command (empty string = use Dockerfile default, `None` = use `bash`) | `None` |
| `platform` | `string` | Platform architecture (e.g., `"linux/amd64"`) | `None` |
| `network` | `string` | Docker network name | `None` |
| `privileged` | `boolean` | Run container in privileged mode | `false` |
| `security_opt` | `list[string]` | Security options | `[]` |
| `cap_add` | `list[string]` | Additional capabilities | `[]` |
| `gpus` | `string` | GPU allocation (e.g., `"all"` or `"device=GPU-UUID"`) | `None` |
| `shm_size` | `string` | Shared memory size (e.g., `"2g"`) | `None` |
| `mem_limit` | `string` | Memory limit (e.g., `"4g"`) | `None` |
| `cpus` | `string` | CPU limit (e.g., `"2"`) | `None` |
| `user` | `string` | User to run as (e.g., `"1000:1000"`) | `None` |
| `working_dir` | `string` | Working directory in container | `None` |

**Build Configuration:**

| Field | Type | Description |
|-------|------|-------------|
| `build_args` | `dict[string, string]` | Docker build arguments |
| `using_cached` | `boolean` | Whether to use cached image (internal flag) |

**Environment, Volumes, and Ports:**

| Field | Type | Description |
|-------|------|-------------|
| `environment` | `dict[string, any]` | Environment variables |
| `volumes` | `list[string]` | Volume mounts in format `"/host:/container:ro"` |
| `mounts` | `list[string]` | Docker mount specifications |
| `ports` | `dict[string, int\|string]` | Port mappings in format `{"port/tcp" = host_port}` |
| `docker_args` | `list[string]` | Raw arguments passed through to Docker CLI |

**Extra Configuration:**

| Field | Type | Description |
|-------|------|-------------|
| `extra` | `dict[string, any]` | Additional custom configuration (e.g., `initializer_timeout_sec`) |

**Kubernetes-Specific Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `pod_name` | `string` | Connect to existing Pod instead of creating new |
| `container_name` | `string` | Name of container within the Pod |

**Example:**

```toml
[sandbox]
backend = "native"
project_relative_shared_data_path = "data/my_project.tar.gz"

[sandbox.sandboxes.main]
image = "ubuntu:20.04"
project_relative_dockerfile_path = "dockerfiles/main/Dockerfile"
timeout = 300

[sandbox.sandboxes.main.build_args]
BASE_IMAGE = "ubuntu:20.04"

[sandbox.sandboxes.main.environment]
PYTHONPATH = "/shared/code"

[sandbox.sandboxes.main.ports]
"8080/tcp" = 8080

[sandbox.sandboxes.main.extra]
initializer_timeout_sec = 1800

[sandbox.sandboxes.joern]
image = "opensage/joern"
project_relative_dockerfile_path = "dockerfiles/joern/Dockerfile"
command = ""

[sandbox.sandboxes.joern.environment]
JAVA_OPTS = "-Xmx16G -Xms4G"

[sandbox.sandboxes.joern.ports]
"8081/tcp" = 18087
```

### LLM Configuration

Configures language models used by agents.

**Section:** `[llm]`

Models are configured under `[llm.model_configs.<model_name>]`:

**Common Model Names:**
- `main`: Primary model for agent reasoning
- `summarize`: Model for summarization and context compression
- `flag_claims`: Model for flag claims processing

**Model Configuration Fields:**

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `model_name` | `string` | Model identifier (e.g., `"openai/gpt-4"`, `"anthropic/claude-3"`) | Required |
| `temperature` | `float` | Sampling temperature (0.0-2.0) | `None` |
| `max_tokens` | `integer` | Maximum tokens in response | `None` |
| `rpm` | `integer` | Rate limit: requests per minute | `None` |
| `tpm` | `integer` | Rate limit: tokens per minute | `None` |

**Example:**

```toml
[llm]

[llm.model_configs.main]
model_name = "openai/gpt-4"
temperature = 0.7
max_tokens = 4096
rpm = 60
tpm = 60000

[llm.model_configs.summarize]
model_name = "openai/gpt-3.5-turbo"
temperature = 0.3
max_tokens = 2048
rpm = 30
tpm = 30000
```

### History Configuration

Configures tool response handling and event history management.

**Section:** `[history]`

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `max_tool_response_length` | `integer` | Maximum length of a single tool response before special handling | `10000` |
| `enable_quota_countdown` | `boolean` | Show remaining LLM call quota after each tool response | `false` |

**Events Compaction Configuration:**

**Section:** `[history.events_compaction]`

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `max_history_summary_length` | `integer` | Character budget threshold for triggering compaction | `100000` |
| `compaction_percent` | `integer` | Percentage of history to compress (0-100) | `50` |

**Example:**

```toml
[history]
max_tool_response_length = 10000
enable_quota_countdown = true

[history.events_compaction]
max_history_summary_length = 100000
compaction_percent = 50
```

### Plugins Configuration

Configures which plugins are enabled.

**Section:** `[plugins]`

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `enabled` | `list[string]` | List of enabled plugin names | `[]` |

**Common Plugins:**
- `history_summarizer_plugin`: Summarizes long conversation history
- `tool_response_summarizer_plugin`: Summarizes long tool responses
- `quota_after_tool_plugin`: Shows quota countdown after tools

**Example:**

```toml
[plugins]
enabled = [
    "history_summarizer_plugin",
    "tool_response_summarizer_plugin",
    "quota_after_tool_plugin",
]
```

### Agent Ensemble Configuration

Configures multi-agent ensemble execution.

**Section:** `[agent_ensemble]`

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `thread_safe_tools` | `list[string]` | List of tool names that are thread-safe (can be called in parallel) | `[]` |
| `available_models_for_ensemble` | `list[string]` or `string` | List of model names available for ensemble (can be comma-separated string) | `[]` |

**Example:**

```toml
[agent_ensemble]
thread_safe_tools = ["google_search", "read_file"]
available_models_for_ensemble = ["openai/gpt-4", "anthropic/claude-3"]
```

Or as comma-separated string:

```toml
[agent_ensemble]
thread_safe_tools = ["google_search", "read_file"]
available_models_for_ensemble = "openai/gpt-4,anthropic/claude-3"
```

### Build Configuration

Configures build and execution commands for target programs.

**Section:** `[build]`

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `poc_dir` | `string` | Directory path for proof-of-concept code | `None` |
| `compile_command` | `string` | Command to compile the target program | `None` |
| `run_command` | `string` | Command to run the target program | `None` |
| `target_type` | `string` | Type of target (e.g., `"default"`, `"binary"`) | `None` |
| `target_binary` | `string` | Path to target binary | `None` |

**Example:**

```toml
[build]
poc_dir = "/tmp/poc"
compile_command = "gcc -o target target.c"
run_command = "./target"
target_type = "binary"
target_binary = "/tmp/poc/target"
```

### MCP Configuration

Configures Model Context Protocol (MCP) services.

**Section:** `[mcp]`

MCP services are configured under `[mcp.services.<service_name>]`:

**Common Service Names:**
- `gdb_mcp`: GDB debugger MCP service
- `pdb_mcp`: PDB debugger MCP service

**MCP Service Configuration Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `sse_port` | `integer` | Server-Sent Events (SSE) server port | Required |
| `sse_host` | `string` | SSE server host (if `None`, uses `default_host` from root config) | `None` |

**Note:** The `sse_host` property dynamically uses `default_host` from the root configuration if not explicitly set.

**Example:**

```toml
[mcp]

[mcp.services.gdb_mcp]
sse_port = 1111

[mcp.services.pdb_mcp]
sse_port = 1112
sse_host = "localhost"  # Optional, defaults to root config's default_host
```

## Complete Example

Here's a complete configuration file example:

```toml
# Template Variables
DEFAULT_IMAGE = "ubuntu:20.04"
MAIN_MODEL = "openai/gpt-4"
NEO4J_PASSWORD = "secure_password"
TASK_NAME = "security_analysis"

# Root Configuration
task_name = "${TASK_NAME}"
src_dir_in_sandbox = "/shared/code"
default_host = "localhost"
auto_cleanup = true

# Neo4j Configuration
[neo4j]
user = "neo4j"
password = "${NEO4J_PASSWORD}"
bolt_port = 7687
neo4j_http_port = 7474

# Sandbox Configuration
[sandbox]
backend = "native"
project_relative_shared_data_path = "data/project.tar.gz"

[sandbox.sandboxes.main]
image = "${DEFAULT_IMAGE}"
project_relative_dockerfile_path = "dockerfiles/main/Dockerfile"
timeout = 300

[sandbox.sandboxes.main.environment]
PYTHONPATH = "/shared/code"

[sandbox.sandboxes.joern]
image = "opensage/joern"
project_relative_dockerfile_path = "dockerfiles/joern/Dockerfile"
command = ""

[sandbox.sandboxes.joern.ports]
"8081/tcp" = 18087

# LLM Configuration
[llm]

[llm.model_configs.main]
model_name = "${MAIN_MODEL}"
temperature = 0.7
max_tokens = 4096

[llm.model_configs.summarize]
model_name = "${MAIN_MODEL}"
temperature = 0.3
max_tokens = 2048

# History Configuration
[history]
max_tool_response_length = 10000
enable_quota_countdown = true

[history.events_compaction]
max_history_summary_length = 100000
compaction_percent = 50

# Plugins Configuration
[plugins]
enabled = [
    "history_summarizer_plugin",
    "tool_response_summarizer_plugin",
]

# Agent Ensemble Configuration
[agent_ensemble]
thread_safe_tools = ["google_search"]
available_models_for_ensemble = "${MAIN_MODEL}"

# Build Configuration
[build]
compile_command = "make"
run_command = "./target"

# MCP Configuration
[mcp]

[mcp.services.gdb_mcp]
sse_port = 1111
```

## Loading Configuration in Code

### Using Default Configuration

```python
import opensage

# Uses default config from src/opensage/templates/configs/default_config.toml
session = opensage.get_session("my_session")
```

### Using Custom Configuration

```python
import opensage

# Load custom configuration file
session = opensage.get_session(
    "my_session",
    config_path="/path/to/my_config.toml",
)
```

### Accessing Configuration

```python
# Access configuration through session
config = session.config

# Access specific sections
neo4j_config = config.neo4j
sandbox_config = config.sandbox
llm_config = config.llm

# Access nested configurations
main_sandbox = config.get_sandbox_config("main")
main_model = config.get_llm_config("main")
```

## Best Practices

1. **Use Template Variables**: Define reusable values as UPPERCASE template variables at the top
2. **Organize by Section**: Group related settings into logical sections
3. **Document Custom Fields**: Add comments for non-standard or custom configuration
4. **Version Control**: Keep configuration files in version control, but exclude sensitive values (passwords, API keys)
5. **Environment-Specific Configs**: Create separate config files for development, testing, and production
6. **Validate Early**: Test configuration files before deploying to catch errors early

## Troubleshooting

### Template Variable Not Found

If you see `KeyError: Template variable 'VAR_NAME' not found`, ensure:
- The variable is defined as an UPPERCASE top-level variable
- The variable name matches exactly (case-sensitive)
- There are no typos in `${VAR_NAME}` references

### Configuration Not Loading

- Verify the TOML file syntax is correct
- Check file path is correct (use absolute paths if relative paths don't work)
- Ensure all required fields are present (check error messages)

### Dynamic Host Resolution

If `default_host` is not set, services like Neo4j and MCP will default to `127.0.0.1`. Set `default_host` at the root level for Kubernetes deployments or remote services.

## Related Documentation

- [Getting Started](Getting-Started.md) - Initial setup guide
- [Core Components](Core-Components.md) - How configuration fits in the system
- [Sandboxes](Sandboxes.md) - Sandbox backends and configuration guide
