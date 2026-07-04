---
icon: lucide/terminal
---


# CLI Reference

OpenSage-ADK provides a command-line interface for interactive development and utility tasks.

## Commands

| Command | Description | Reference |
|---------|-------------|-----------|
| `opensage` | Main entry point | Run `uv run opensage --help` |
| [`opensage web`](opensage-web.md) | Interactive web UI for agent development | Run `uv run opensage web --help` |
| `opensage run` | Terminal agent runner without the web UI | Run `uv run opensage run --help` |
| `opensage review` | Read-only session viewer for saved trajectories | Run `uv run opensage review --help` |
| [`opensage dependency-check`](dependency-check.md) | Verify external dependencies | Run `uv run opensage dependency-check --help` |


```text
Usage: opensage [OPTIONS] COMMAND [ARGS]...

  OpenSage CLI tools.

Options:
  --help  Show this message and exit.

Commands:
  dependency-check  Check OpenSage external dependencies.
  review            Read-only session viewer — no LLM, no sandbox, no core...
  run               Run an agent interactively in the terminal (no web UI).
  web               Starts an OpenSage-flavored Web UI: prepare...
```

## `opensage run`

Runs an agent from the terminal instead of starting the web UI.

```bash
uv run opensage run --agent /path/to/agent_dir --config /path/to/config.toml
```

| Flag | Purpose |
|------|---------|
| `--config` | Path to the OpenSage TOML config |
| `--agent` | Path to the agent folder |
| `--log_level` | Logging level: `debug`, `info`, `warning`, `error`, or `critical` |
| `--auto_cleanup` | Whether to clean up sandboxes on process exit |
| `--resume` | Resume the most recently saved session |
| `--resume-from` | Resume a specific saved session |
| `--prompt` | Send one prompt, run to completion, then exit |

## `opensage review`

Opens a read-only Web UI for a saved session. It does not start LLM calls, sandboxes, or core runtime services.

```bash
uv run opensage review ~/.local/opensage/sessions/<session_dir>
```

| Argument / Flag | Purpose |
|-----------------|---------|
| `SESSION_PATH` | Optional saved session directory, directory name, or suffix. Defaults to the most recent saved session |
| `--host` | Binding host for the viewer |
| `--port` | Port for the viewer |
| `--log_level` | Logging level |
| `--live` | Poll `traj.json` files for updates while watching a running `opensage run` session |


## `opensage web` vs Evaluations

| Aspect | `opensage web` | Evaluations |
|--------|----------------|-------------|
| **Use Case** | Development, debugging | Performance measurement |
| **Interaction** | Interactive chat | Batch processing |
| **Sessions** | Single long-lived | Multiple short-lived |
| **Parallelism** | Single user | Multiple tasks |
| **Output** | Real-time events | Saved results files |

See also: [Evaluations](../developer-guide/evaluation/index.md)
