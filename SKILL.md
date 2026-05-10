---
name: opensage-adk
description: Technical reference for OpenSage-ADK — a Google ADK-based framework for long-horizon, tool-heavy AI agents. Covers architecture, configuration, customization, extension, and production agent patterns.
---

# OpenSage-ADK

OpenSage-ADK is an agent framework built on Google ADK for long-horizon, tool-heavy tasks. It unifies six subsystems — sessions, sandboxes, tools, plugins, history management, and multi-agent orchestration — into a single deterministic platform driven by a TOML config and an `agent.py` factory.

## Installation

Requirements: Python 3.12+, `uv`, Docker.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/opensage-agent/opensage-adk.git
cd opensage-adk
uv venv --python 3.12
uv sync
uv run opensage --help
```

## Minimal Agent

An agent directory contains `agent.py` with a `mk_agent()` factory. The factory receives the current session ID and returns an `OpenSageAgent`.

```python
# my_agent/agent.py
import os
from google.adk.models.lite_llm import LiteLlm
import opensage
from opensage.agents import MemoryManagement, OpenSageAgent

def mk_agent(opensage_session_id: str, model=None):
    session = opensage.get_opensage_session(opensage_session_id)
    if model is None:
        model = LiteLlm(model="anthropic/claude-opus-4-7")
    return OpenSageAgent(
        name="my_agent",
        description="My agent.",
        model=model,
        instruction="You are a helpful assistant.",
        enabled_skills="all",
        memory_management=MemoryManagement.FILE,
        tools=[],
        sub_agents=[],
    )
```

Launch the web UI:

```bash
uv run opensage web --agent /path/to/my_agent --port 8000
```

LiteLLM resolves credentials from environment (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.).

## Architecture

### Session lifecycle

Every run follows seven phases:

1. Input parsing and logging setup.
2. `opensage.get_session(session_id, config_path)` instantiates an `OpenSageSession` holding configuration, sandbox manager, Neo4j client manager, dynamic-agent manager, and ensemble manager.
3. Sandbox dependency collection from a dummy agent, followed by pruning of unused sandboxes.
4. Shared-volume initialization.
5. Container launch and per-sandbox initializer execution.
6. Agent loading and plugin discovery.
7. ADK runner event loop.

Entry points (`opensage web`, evaluation runners, RL integrations) share phases 1–4 and 7; phases 5–6 diverge. `get_opensage_session(session_id)` returns the active session from anywhere in tool code.

### Project structure (`src/opensage/`)

- `agents/` — `OpenSageAgent`, `ToolLoader`
- `sandbox/` — backends (`native`, `remotedocker`, `opensandbox`, `agentdocker-lite`, `local`, `k8s`) and initializers (`main`, `neo4j`, `joern`, `codeql`, `gdb_mcp`, `pdb_mcp`, `fuzz`)
- `session/` — managers
- `bash_tools/` — bash-scripted Skills
- `toolbox/` — Python tools and MCP toolsets
- `config/` — TOML loading and dataclasses
- `plugins/` — ADK plugins and Claude Code hooks
- `memory/` — Neo4j-backed short- and long-term memory
- `features/` — feature flags, summarization, `ToolCombo`
- `evaluations/` — benchmarks

### Sessions and managers

`OpenSageSession` owns five managers:

- `DynamicAgentManager` — spawned sub-agents with states `CREATED`, `ACTIVE`, `PAUSED`, `STOPPED`, `PENDING_TOOLS`, `ERROR`.
- `OpenSageSandboxManager` — container lifecycle and shared volumes.
- `OpenSageNeo4jClientManager` — lazy Neo4j clients.
- `OpenSageEnsembleManager` — parallel model fan-out.
- `MessageBoardManager` — append-only JSONL boards for ensemble coordination.

### Sandboxes

Two orthogonal axes separate concern: **backend** (where execution happens — Docker local/remote, Kubernetes, native) from **initializer** (what setup runs inside). A session owns multiple named sandboxes declared in `[sandbox.sandboxes.<name>]`. Shared mounts `/shared` (target code) and `/mem/shared` (file-based IPC) bind into every sandbox.

Tools declare sandbox dependencies via `@requires_sandbox(...)` or a `## Requires Sandbox` section in `SKILL.md`. `collect_sandbox_dependencies()` prunes unused sandboxes at startup. Lifecycle per run: shared-volume init → parallel container launch → per-sandbox initializers → MCP readiness polling.

### Tools

`OpenSageAgent` normalizes five tool shapes into one dict via `make_toollikes_safe_dict()`:

- Plain Python functions (signature + docstring → JSON schema)
- `AgentTool` wrapping a sub-agent
- `BaseToolset` (batches unfolded recursively)
- `MCPToolset` / `OpenSageMCPToolset` (tools discovered over MCP)
- `ToolCombo` (sequential chain wrapped as an atomic tool)

Wrappers preserve exception handling (errors become `{"success": false, "error": "..."}`) and return structure (raw values wrap into `{"result": ...}`, dicts pass through).

Built-in toolbox categories: `general/` (`bash_tool_main`, `run_terminal_command`, `view_file`, `str_replace_edit`, `WebSearchTool`, `think`, `complain`, `critique`), `retrieval/`, `static_analysis/`, `debugger/`, `binary/`, `fuzzing/`, `coverage/`, `neo4j/`, `benchmark_specific/`.

**Bash Skills** live in `src/opensage/bash_tools/` or `~/.local/opensage/bash_tools/` as directories containing `SKILL.md` plus `scripts/`. `ToolLoader` loads them based on the agent's `enabled_skills` value: `None` (no skills), `"all"` (top-level only), or a list of prefixes (e.g., `["fuzz", "retrieval/grep"]`).

**MCP integration**: `OpenSageMCPToolset` wraps ADK's `McpToolset` with name stability and `tool_name_prefix` enforcement. Server lifecycle: sandbox config declares the service, the initializer starts the process, readiness is polled, and tools are discovered on first call.

### Plugins

Plugins hook `after_tool_callback`, `on_event_callback`, `before_model_callback` (and related lifecycle callbacks) in strict sequential order, mutating shared state.

Two kinds:

- **ADK plugins** — Python `.py` subclassing `google.adk.plugins.base_plugin.BasePlugin`.
- **Claude Code hooks** — declarative JSON with matchers (`PreToolUse`/`PostToolUse`, exact/pipe-separated names, argument globs, wildcards) and actions (`prompt` or `command`).

Discovery priority (later shadows earlier):

1. `src/opensage/plugins/default/adk_plugins/`
2. `src/opensage/plugins/default/claude_code_hooks/`
3. `extra_plugin_dirs` entries
4. `{agent_dir}/plugins/`

Available callbacks: `before_tool_callback`, `after_tool_callback`, `on_tool_error_callback`, `before_model_callback`, `after_model_callback`, `on_model_error_callback`, `before_agent_callback`, `after_agent_callback`, `before_run_callback`, `after_run_callback`, `on_user_message_callback`, `on_event_callback`.

Built-in plugins: `history_summarizer_plugin`, `tool_response_summarizer_plugin`, `quota_after_tool_plugin`, `doom_loop_detector_plugin`, `build_verifier_plugin`, `memory_observer_plugin`, `image_injection_plugin`, `read_before_edit_plugin`, `message_board_diff_plugin`.

### History: truncation vs compaction

Two levers prevent context overflow:

- **Truncation** (per response, `tool_response_summarizer_plugin`): if output exceeds `max_tool_response_length` (default 10000), summarize or truncate to preview + file pointer `/workspace/.tool_outputs/<id>`; full output persists in the sandbox.
- **Compaction** (whole log, `history_summarizer_plugin`): if folded event characters exceed `max_history_summary_length` (default 100000), `OpenSageFullEventSummarizer` takes the first `compaction_percent` of events (default 50) after the last boundary, expands to a paired call-response boundary, summarizes via LLM, and replaces the window with a single `EventCompaction` node.

When `[history] enable_quota_countdown = true`, `quota_after_tool_plugin` appends `{used, remaining, limit}` to responses. Short-term backends: file-based (default, `~/.local/opensage/sessions/`) or Neo4j-backed. Long-term memory in `src/opensage/memory/database_based/long_term/` persists patterns across sessions.

### Multi-agent composition

Three patterns:

1. **Static `AgentTool`** — wrap known sub-agents at construction.
2. **Dynamic sub-agents** — `create_subagent(name, model, instruction, tools_list, enabled_skills)` returns an agent id with states `CREATED → ACTIVE → PAUSED/STOPPED/ERROR/PENDING_TOOLS`; `call_subagent_as_tool(subagent_id, request)` invokes; `list_active_agents()` introspects. `tools_list` validates against parent tool names; MCP `tool_name_prefix` pulls whole toolsets; unmet tools park the child in `PENDING_TOOLS`.
3. **Ensembles** — `agent_ensemble(agent_name, instruction, model_name_to_count)` or `agent_ensemble_pairwise` fan instruction across models in parallel and aggregate via the `summarize` profile.

Message boards (`MessageBoardManager`, `message_board.py`) are append-only JSONL with lock-free writes and per-agent read cursors; `message_board_diff_plugin` surfaces new entries as `_message_board_diff`.

Self-reflection tools: `think`, `plan`, `complain`, `note_suspicious_things`, `critique` (spins a separate model), `flag_unjustified_claims`.

`ToolCombo` chains tools via a `SequentialAgent`; `return_history=True` exposes intermediate steps to the parent, `False` wraps the chain in an `AgentTool` so the parent sees only the final result.

## CLI Reference

### `opensage web`

| Flag | Default | Purpose |
|------|---------|---------|
| `--config FILE` | `<agent_dir>/config.toml` if present | TOML config path |
| `--agent DIRECTORY` | required | Agent folder |
| `--host TEXT` | `127.0.0.1` | Binding host |
| `--port INTEGER` | `8000` | Server port |
| `--log_level [debug\|info\|warning\|error\|critical]` | `info` | Log verbosity |
| `--auto_cleanup BOOLEAN` | `false` | Clean up sandboxes on exit; `false` saves snapshot to `~/.local/opensage/sessions/<agent_name>_<session_id>` |
| `--resume` | — | Resume most recent session |
| `--resume-from TEXT` | — | Resume specific session (directory name, bare ID suffix, or absolute path); implies `--resume` |

### `opensage dependency-check`

No flags. Reports CodeQL, Docker, and kubectl availability.

## Configuration Reference

TOML with template variables: top-level UPPERCASE keys can be referenced as `${VAR_NAME}` anywhere. Loading order: default template (`src/opensage/templates/configs/default_config.toml`) → custom config from `--config`.

### Root fields

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `task_name` | string | `None` | Session name |
| `src_dir_in_sandbox` | string | `/shared/code` | Source directory inside containers |
| `agent_storage_path` | string | `None` | Storage for dynamically created agents |
| `default_host` | string | `127.0.0.1` | Default host for sidecar services |
| `auto_cleanup` | bool | `true` | Clean resources on session end |

### `[llm]`

Profiles under `[llm.model_configs.<profile>]`. Built-in profiles: `main` (required), `summarize` (fall back to `main` if absent).

Fields: `model_name` (required), `temperature`, `max_tokens`, `rpm`, `tpm`.

```toml
[llm.model_configs.main]
model_name = "anthropic/claude-opus-4-7"
temperature = 0.7
max_tokens = 8192
rpm = 60

[llm.model_configs.summarize]
model_name = "anthropic/claude-haiku-4-5"
temperature = 0.3
```

### `[sandbox]`

Top-level: `default_image`, `backend` (`native` stable; `remotedocker`, `opensandbox`, `agentdocker-lite`, `local`, `k8s` under development), `project_relative_shared_data_path`, `absolute_shared_data_path`, `host_shared_mem_dir`, `mount_host_paths` (list of `"/host:/container[:ro|rw]"`), `tolerations` (k8s only).

Per-sandbox `[sandbox.sandboxes.<name>]` built-ins: `main`, `joern`, `codeql`, `neo4j`, `gdb_mcp`, `pdb_mcp`, `fuzz`.

Container fields: `image`, `container_id`, `timeout` (default 300), `project_relative_dockerfile_path`, `absolute_dockerfile_path`, `command`, `platform`, `network`, `privileged`, `security_opt`, `cap_add`, `gpus`, `shm_size`, `mem_limit`, `cpus`, `user`, `working_dir`.

Build: `build_args`, `using_cached`.

Env/volumes/ports: `environment`, `volumes`, `mounts`, `ports`, `docker_args`, `extra`.

Kubernetes: `pod_name`, `container_name`.

```toml
[sandbox]
backend = "native"
default_image = "ubuntu:22.04"
absolute_shared_data_path = "/tmp/shared"

[sandbox.sandboxes.main]
image = "ubuntu:22.04"
mem_limit = "4g"
cpus = "2"
environment = {PYTHONUNBUFFERED = "1"}
volumes = ["/host/code:/shared/code:ro"]

[sandbox.sandboxes.joern]
image = "joern/joern:latest"
timeout = 600
```

### `[mcp]`

```toml
[mcp.services.gdb_mcp]
sse_port = 8001
sse_host = "127.0.0.1"

[mcp.services.pdb_mcp]
sse_port = 8002
```

Pair each `[mcp.services.<name>]` with `[sandbox.sandboxes.<name>]` so the sandbox launches the server and MCP knows where to reach it. Fields: `sse_port` (required), `sse_host` (falls back to `default_host`).

### `[history]`

```toml
[history]
max_tool_response_length = 10000
enable_quota_countdown = true

[history.events_compaction]
max_history_summary_length = 100000
compaction_percent = 50
```

### `[plugins]`

```toml
[plugins]
enabled = ["doom_loop_detector_plugin", "history_summarizer_plugin"]
extra_plugin_dirs = ["/path/to/shared/plugins"]

[plugins.adk_plugin_params.doom_loop_detector_plugin]
threshold = 5
```

Fields: `enabled` (names or regex patterns), `extra_plugin_dirs`, `adk_plugin_params` (per-plugin kwargs).

### `[agent_ensemble]`

```toml
[agent_ensemble]
thread_safe_tools = ["google_search", "read_file"]
available_models_for_ensemble = ["main", "summarize"]
```

Fields: `thread_safe_tools` (parallel-safe; others serialize), `available_models_for_ensemble` (list or comma-separated string).

### `[neo4j]`

```toml
[neo4j]
user = "neo4j"
password = "mypassword"
bolt_port = 7687
neo4j_http_port = 7474
```

URI constructed at runtime as `neo4j://{default_host}:{bolt_port}`. Declare the Neo4j sandbox separately under `[sandbox.sandboxes.neo4j]`.

### `[build]`

```toml
[build]
poc_dir = "/tmp/poc"
compile_command = "gcc -o target target.c"
run_command = "./target"
target_type = "binary"
target_binary = "/tmp/poc/target"
```

## Customization Recipes

### Agents 101

- **Minimal agent** — one `OpenSageAgent` with one Python function tool (docstring + type hints auto-generate the schema).
- **Custom Python tool** — any callable with a docstring becomes a tool.
- **MCP stdio** — `MCPToolset(connection_params=StdioConnectionParams(server_params=StdioServerParameters(command="npx", args=[...])))`.
- **MCP SSE** — `MCPToolset(connection_params=SseConnectionParams(url="http://127.0.0.1:3001/sse"))`.
- **MCP server-as-tool** — `MCPToolset(connection_params=StreamableHTTPConnectionParams(url="http://0.0.0.0:9998/mcp"))`.

### Agents with features

- **ToolCombo** — chain tools into one atomic call. Knob: `return_history` (True exposes intermediate steps; False wraps the chain).
- **Dynamic sub-agents** — pass `create_subagent`, `list_active_agents`, `call_subagent_as_tool` into the root `tools`. Set `agent_storage_path` to persist across runs.
- **Summarization** — enable `history_summarizer_plugin` and `tool_response_summarizer_plugin` in `[plugins] enabled`; tune `max_history_summary_length` and `max_tool_response_length`.
- **Neo4j logging** — set `memory_management=MemoryManagement.DATABASE` on the root agent and configure `[neo4j]`.
- **Agent ensemble** — pass `agent_ensemble`, `get_available_agents_for_ensemble`, `get_available_models` into root `tools`; set `[agent_ensemble] available_models_for_ensemble`.
- **Web search** — add `WebSearchTool(search_context_size="medium")` to `tools`, or `GoogleSearchTool()` with Gemini.

## Developer Guide

### Adding Python tools

Define a callable with a docstring (description) and type-annotated parameters. Return structured values.

```python
def greet(name: str) -> str:
    """Return a friendly greeting."""
    return f"Hello, {name}!"
```

### Adding Bash Skills

Layout under `src/opensage/bash_tools/{category}/{tool-name}/`:

```
tool-name/
├── SKILL.md         # YAML frontmatter + markdown doc
└── scripts/
    └── tool_script.sh
```

`SKILL.md` frontmatter fields: `name`, `description`, `should_run_in_sandbox: main`. Body sections document parameters (positional/named/flag), return JSON, required sandboxes (`## Requires Sandbox`), and timeout. Scripts emit `{"success": true, "result": "..."}` on stdout; exit non-zero on error.

Auto-discovery from `src/opensage/bash_tools/` and `~/.local/opensage-adk/bash_tools/`. The agent's `enabled_skills` gates which load. Optional `deps/install.sh` or `deps/<sandbox_type>/install.sh` runs once per session during sandbox init (marker under `/shared`).

### Adding MCP toolsets

```python
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseConnectionParams

@safe_tool_execution
@requires_sandbox("gdb_mcp")
def get_toolset(session_id: str) -> MCPToolset:
    url = get_mcp_url_from_session_id("gdb_mcp", session_id)
    return MCPToolset(connection_params=SseConnectionParams(url=url))
```

Register in the agent's `tools` list.

### Adding plugins

Subclass `google.adk.plugins.base_plugin.BasePlugin` and override any lifecycle callback. Declarative Claude Code hooks live as `.json` files with matchers and actions. Enable by name in `[plugins] enabled`.

### Adding a sandbox type (initializer)

1. Create under `src/opensage/sandbox/initializers/` subclassing `SandboxInitializer` (`base.py`).
2. Implement `async def async_initialize(self) -> None`.
3. Register in `src/opensage/sandbox/factory.py` (`SANDBOX_INITIALIZERS` dict).
4. Add config fields to `src/opensage/config/config_dataclass.py`.
5. Configure TOML: `[sandbox.sandboxes.my_sandbox] image = "my_image:tag"`.

For Python deps in the image, install `uv`, create a venv under `/app`, and run `uv pip install`. Activate the venv explicitly per command: `/app/.venv/bin/python ...`.

### Adding a sandbox backend

1. Subclass `BaseSandbox` (`src/opensage/sandbox/base_sandbox.py`) — see `native_docker_sandbox.py`, `remote_docker_sandbox.py`, `k8s_sandbox.py`, `local_sandbox.py`.
2. Register in `src/opensage/sandbox/factory.py` (`SANDBOX_BACKENDS` dict).
3. Optionally implement `set_config()` classmethod.
4. Select with `[sandbox] backend = "mybackend"`.

Backends own container/runtime management only; initializers own what to install.

### Evaluations

Subclass `Evaluation` (`src/opensage/evaluation/base.py`):

- **Required:** `_get_task_id(sample)`, `_get_first_user_message(sample)`.
- **Optional:** `_get_dataset()`, `_create_task(sample)`, `_get_export_dir_in_sandbox(sample)`, `customized_modify_and_save_results(...)`, `evaluate()`.

Invoke via Python Fire:

```bash
python -m opensage.evaluations.<benchmark>.<module> <method> [options]
```

Methods: `run` (auto parallelism + eval), `run_debug` (single-threaded + eval), `generate` (multiprocessing only), `generate_threaded`, `generate_single_thread`.

Common options: `dataset_path` (required), `agent_dir` (required), `max_llm_calls` (100), `max_workers` (6), `use_multiprocessing` (True), `use_sandbox_cache` (True), `run_until_explicit_finish` (True), `use_config_model` (False), `llm_retry_count` (3), `llm_retry_timeout` (30), `log_level` ("INFO").

Per-sample lifecycle: `_create_task` → `_prepare_environment` (launch sandboxes, restore cache) → `_load_mk_agent` → `_run_agent` → `_collect_outputs` (export, save traces, cost) → cleanup.

Output tree:

```
evals/<eval>/yymmdd_HHMMSS/
├── evaluation_master.log
├── eval_params.json
└── task_001/
    ├── execution_debug.log, execution_info.log
    ├── config_used.toml
    ├── cost_info.json
    ├── session_trace.{json,txt}
    ├── metadata.json
    ├── sandbox_output/
    └── neo4j_history/
```

Built-in benchmarks:

- **CyberGym** — static/dynamic/vuln-detection variants; needs PoC submission server on port 8666.
- **SWE-Bench Pro** — software engineering; flags `--use_explore_agent`, `--skip_existing`, `--task_file`.
- **SeCodePLT** — vulnerability detection with CodeQL; needs CodeQL bundle under `sandbox_scripts/codeql/`.

### RL integration

**AREAL** — SGLang rollouts (TP=2) + FSDP trainer (DP=2) + GRPO on SeCodePLT.

```bash
git clone --recurse-submodules -b adk https://github.com/rucnyz/AReaL
cd AReaL && pip install uv && uv sync --extra cuda
bash examples/opensage/run_opensage_grpo.sh --trial my_experiment
```

Key config in `examples/opensage/opensage_grpo_mt.yaml`: `actor.path` (default `Qwen/Qwen3-4B`), `gconfig.max_new_tokens` (8192), `gconfig.n_samples` (4), `max_tokens_per_mb` (65536), `agent_run_args.max_turns` (20), `log_raw_conversation` (true).

**SLIME** — SGLang rollout in a SLIME container + Megatron-LM trainer on SeCodePLT or mock.

```bash
git clone https://github.com/rucnyz/slime && cd slime && git checkout opensage
docker compose up --build
pip install -e /root/opensage
python opensage_mock.py --local_dir /root/opensage_data --output_filename mock_tasks.jsonl
bash /root/opensage/rl/slime/train.sh --benchmark secodeplt --gpus 2,3 --slime-config rl/slime/configs/secodeplt.yaml
```

Config files in `rl/slime/configs/*.yaml` tune `num-rollout`, `rollout-batch-size`, `global-batch-size`, `lr`, `kl-loss-coef`, `save-interval`, `eval-interval`. Integration code lives in `src/opensage/rl_integration/` (`SlimeLlm`, `SlimeAdapter`, `Client`, `BenchmarkInterface`).

## Production Agents

| Agent | Domain | Key tools | Distinctive feature |
|-------|--------|-----------|---------------------|
| `harbor_agent` | T-Bench software engineering | `view_file`, `str_replace_edit`, `run_terminal_command`, background tasks | Single-agent, minimal toolset, `enabled_skills=None` |
| `devops_gym_agent` | DevOps-Gym (build, monitoring, test) | File ops, terminal, `think`, `agent_ensemble_pairwise` | Planning as a first-class tool; deadlock recovery via pairwise ensemble |
| `swebenchpro_agent` | SWE-Bench Pro bug fixing | File ops, `search_memory`, sub-agents | Two-phase explorer + solver; long-term graph memory (Neo4j) |
| `ctf_agent` | CTF binary reverse engineering | Ghidra, IDA Pro, pyghidra, GDB MCPs | MCP-in-subagent — each sub-agent holds only its MCP; `cache_control_injection_points` on system + last two messages |
| `debugger_agent` | Live program verification (sub-agent) | GDB MCP, bash | Hypothesis-only debugging; budget guard (`remaining LLM calls < 3`) |
| `vul_agent_static_tools` | Vulnerability classification | Joern CPG, Neo4j queries, grep | Tight toolset; designed as an ensemble member |
| `poc_agent_static_tools` | PoC generation (static only) | Neo4j, bash, retrieval, static analysis | Mandatory dynamic sub-agents; path-before-PoC discipline; CyberGym oracle via `generate_poc_and_submit` |
| `poc_agent_dynamic_tools` | PoC generation (hybrid) | GDB MCP, fuzzing, coverage, Neo4j | Static first → fuzzing → debugger escalation; local verification via `run_poc_from_script` |
| `patch_agent` | Scaffold template | `bash_tool_main` | Minimal `mk_agent()` stub for new agents |

Each production agent ships with its `agent.py` and `config.toml(s)` under `examples/agents/` and can be launched with `uv run opensage web --agent examples/agents/<name>`.

## Best Practices

- Prefer `opensage.get_session()` over manual construction; it expands the config.
- Call `opensage.cleanup_session(session_id)` to release sandboxes.
- Keep each agent focused on one responsibility; use sub-agents for hand-offs.
- Document tool parameters and returns in docstrings — the LLM reads them.
- Use `${VAR_NAME}` template variables for environment-specific values.
- Emit structured logs with `extra` fields: `logger.info("msg", extra={"session_id": session_id})`.

## Troubleshooting

- **Tests** — `uv run pytest tests/` (add `--cov=src/opensage` for coverage).
- **Debug** — `uv run opensage web --config config.toml --agent agent_dir --port 8080 --neo4j_logging`.
- **Direct sandbox access** — `session.sandboxes.get_sandbox("main").run_command_in_container("pwd")`.
- **Missing credentials** — set `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` in the environment before launch.
- **Template variables** — must be UPPERCASE and match exactly in `${VAR_NAME}`.
- **Remote services** — set `default_host` (defaults to `127.0.0.1`) for k8s or remote Neo4j / MCP.
