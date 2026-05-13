---
icon: lucide/settings
---


# Customize Agents

An OpenSage-ADK agent can be built from a handful of rich capabilities. Before you start customizing, here's what each one does:

- **Sessions**: the per-run state that holds the agent's message history, scratch memory, and sandbox handles.
- **Sandboxes**: the isolated environments (containers or native) where every tool call actually executes. Your host stays safe; multiple sandboxes can run side-by-side (main workspace, MCP sidecars, Neo4j, and so on).
- **Tools & MCP**: the verbs your agent can use, including Python function tools, bash tools, and external MCP services (filesystem, GDB, custom).
- **History**: truncation and summarization strategies that keep long runs from overflowing the context window.
- **Plugins**: small opt-in behaviors that hook into the agent's lifecycle (doom-loop detection, tool-response summarization, build verification, and similar).
- **Multi-agent**: patterns for orchestration: sub-agents as tools, dynamically-spawned sub-agents, and ensembles that fan a sub-task across several sub-agents.

The `config.toml` file is the **place where you wire those capabilities together**.

## Start from Nothing

You do **not** need to write a `config.toml` to run an agent. Launch with just an `--agent` path and OpenSage-ADK will load the built-in default from [`src/opensage/templates/configs/default_config.toml`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/templates/configs/default_config.toml):

```bash
uv run opensage web --agent agent_library/agents_101/sample_agent
```

That default gives you a working LLM client, a native sandbox, and basic history handling: enough to try the [Basic Agent](../examples/agents_101/sample_agent.md) example. Once you want to change the model, point at a different sandbox, or enable a feature, you write your own `config.toml` and override only the fields that differ from the default.

Every override goes in a TOML file and is loaded via `--config`:

```bash
uv run opensage web --agent my_agent --config my_agent/config.toml
```


## OpenSage's Configuration System

Configs are loaded in this order:

1. **Default**: `src/opensage/templates/configs/default_config.toml` (always loaded).
2. **Custom**: whatever you pass via `--config` on the CLI, or `config_path=` when you construct a session in Python. Values here override the default.

The configuration file has the following structure

```toml title="config.toml"
# --- Top-level template variables (optional, UPPERCASE) ---
VARIABLE_NAME = "value"

# --- Root-level fields ---
task_name = "my_task"
src_dir_in_sandbox = "/shared/code"
default_host = "127.0.0.1"
auto_cleanup = true

# --- Section-based configuration ---
[llm]             # Language models (step 1)
[sandbox]         # Execution environments (step 2)
[mcp]             # MCP tool services (step 3)
[history]         # Tool response + event compaction (step 4)
[plugins]         # Plugin opt-in list (step 5)
[agent_ensemble]  # Multi-model ensembles (step 6)
[neo4j]           # Graph database (step 7)
[build]           # Build/run commands (step 8)
```

## Template Variables

Top-level UPPERCASE keys automatically become template variables and can be referenced anywhere in the file with `${VAR_NAME}`. Expansion is recursive, and unresolved variables raise an error at load time.

```toml title="config.toml"
DEFAULT_IMAGE = "ubuntu:24.04"
MAIN_MODEL    = "anthropic/claude-opus-4-7"

[sandbox.sandboxes.main]
image = "${DEFAULT_IMAGE}"

[llm.model_configs.main]
model_name = "${MAIN_MODEL}"
```

This keeps the "which model/which image" choice in one place when a config references it in several spots.

## Root-Level Fields

A few common fields sit outside any `[section]`: `task_name`, `src_dir_in_sandbox`, `agent_storage_path`, `default_host`, `auto_cleanup`. See the [Configuration Reference](../../reference/configuration/index.md#top-level-root-fields) for the full list, types, and defaults.


## Section-Based Configuration

Follow the steps in order, or jump straight to the one you need:

1. [**Pick an LLM**](./llm.md): the one setting every agent needs.
2. [**Choose a Sandbox**](./sandbox.md): where tool calls actually run.
3. [**Add MCP Tools**](./mcp.md): wire in external tool servers (GDB, filesystem, custom).
4. [**Tune Memory & History**](./history.md): keep long runs from blowing context.
5. [**Turn on Plugins**](./plugins.md): small behaviors you opt in to.
6. [**Agent Ensembles**](./agent-ensemble.md): fan a sub-task across multiple models and aggregate results.
7. [**Neo4j Logging**](./neo4j.md): point the memory subsystem at a Neo4j instance.
8. [**Extra Building**](./build.md): set `compile_command` / `run_command` for agents that build and run user-supplied code.

Once your `config.toml` is shaped, see the [**Complete Example**](./complete-example.md) for a fully-featured template you can adapt.

## Loading Configuration in Python

```python title="my_agent.py"
import opensage

# Use the built-in default
session = opensage.get_session("my_session")

# Or load a custom file
session = opensage.get_session(
    "my_session",
    config_path="/path/to/my_config.toml",
)

# Read values back
config       = session.config
main_sandbox = config.get_sandbox_config("main")
main_model   = config.get_llm_config("main")
```
## Best Practices

1. **Use template variables** for any value referenced more than once: model name, image name, password.
2. **Start from the default** and override only the sections you need; empty sections inherit.
3. **Split by environment**: separate `config.dev.toml` / `config.prod.toml` rather than hand-editing before each run.
