# Complete Example

Here's a complete example `config.toml` that uses most of the features covered in the previous sections. You can use this as a template for your own agents, or refer to it when you want to see how different configurations fit together.

```toml title="config.toml"
# --- Template Variables ---
DEFAULT_IMAGE   = "ubuntu:24.04"
MAIN_MODEL      = "anthropic/claude-opus-4-7"
SECOND_MODEL    = "openai/gpt-5"
NEO4J_PASSWORD  = "secure_password"
TASK_NAME       = "security_analysis"

# --- Root Configuration ---
task_name          = "${TASK_NAME}"
src_dir_in_sandbox = "/shared/code"
default_host       = "localhost"
auto_cleanup       = true

# --- LLM ---
[llm.model_configs.main]
model_name  = "${MAIN_MODEL}"
temperature = 0.7
max_tokens  = 4096

[llm.model_configs.summarize]
model_name  = "${MAIN_MODEL}"
temperature = 0.3
max_tokens  = 2048

# --- Sandbox ---
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

# --- MCP ---
[mcp.services.gdb_mcp]
sse_port = 1111

# --- Agent Ensemble ---
[agent_ensemble]
thread_safe_tools             = ["google_search"]
available_models_for_ensemble = "${MAIN_MODEL},${SECOND_MODEL}"

# --- Plugins ---
[plugins]
enabled = [
    "doom_loop_detector_plugin",
    "history_summarizer_plugin",
    "tool_response_summarizer_plugin",
    "quota_after_tool_plugin",
]
extra_plugin_dirs = []

# --- History ---
[history]
max_tool_response_length = 10000
enable_quota_countdown   = true

[history.events_compaction]
max_history_summary_length = 100000
compaction_percent         = 50

# --- Neo4j ---
[neo4j]
user             = "neo4j"
password         = "${NEO4J_PASSWORD}"
bolt_port        = 7687
neo4j_http_port  = 7474

# --- Build ---
[build]
compile_command = "make"
run_command     = "./target"
```
