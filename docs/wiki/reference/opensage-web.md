---
icon: lucide/monitor-check
---

# WebUI Reference

> Full flag reference: [`opensage web --help`](../generated/cli/opensage-web.md)

The `opensage web` command starts an interactive web UI for developing and debugging agents.

## Usage

```bash
uv run opensage web \
  --config /path/to/config.toml \
  --agent /path/to/agent_dir \
  --port 8000
```

## Flags by Task


```text
Usage: opensage web [OPTIONS]

  Starts an OpenSage-flavored Web UI: prepare environment then serve agents.

Options:
  --config FILE                   Path to OpenSage TOML config. If omitted,
                                  defaults to <agent_dir>/config.toml when
                                  present.
  --agent DIRECTORY               Path to the agent folder (must contain agent
                                  files).
  --host TEXT                     Binding host for the server.  [default:
                                  127.0.0.1]
  --port INTEGER                  Port for the server.  [default: 8000]
  --log_level [debug|info|warning|error|critical]
                                  Logging level for the server.  [default:
                                  INFO]
  --auto_cleanup BOOLEAN          Whether to cleanup sandboxes on process
                                  exit. When false, session snapshots are
                                  saved to ~/.local/opensage/sessions/<agent_n
                                  ame>_<session_id>.  [default: False]
  --resume                        Resume from the most recently saved session
                                  under ~/.local/opensage/sessions.
  --resume-from TEXT              Resume from a specific saved session.
                                  Accepts a saved session directory name, a
                                  bare session id suffix, or an absolute path
                                  to a saved session directory. Implies
                                  --resume.
  --help                          Show this message and exit.
```


The curated subset below covers what users hit first. See the [full `--help`](../generated/cli/opensage-web.md) for everything.

### Starting a Session

| Flag | Purpose | Default |
|------|---------|---------|
| `--config` | Path to the OpenSage TOML config | `<agent_dir>/config.toml` if present |
| `--agent` | Path to the agent folder (must contain `agent.py`) | Required |
| `--host` | Binding host for the server | `127.0.0.1` |
| `--port` | Port for the server | `8000` |

### Session Persistence and Resume

`opensage web` can snapshot sandbox state on exit so a session can be resumed later. Snapshots live at `~/.local/opensage/sessions/<agent_name>_<session_id>/`.

| Flag | Purpose |
|------|---------|
| `--auto_cleanup` | Whether to cleanup sandboxes on exit. When `false`, a snapshot is saved. Default: `false` |
| `--resume` | Resume from the most recently saved session |
| `--resume-from` | Resume a specific session (accepts directory name, bare session-id suffix, or absolute path). Implies `--resume` |

```bash
# Start a new session; snapshot on exit
uv run opensage web --config opensage.toml --agent my_agent

# Resume the latest saved snapshot
uv run opensage web --resume

# Resume a specific snapshot
uv run opensage web --resume-from my_agent_c0606edc-2fff-496d-8964-48bdd7f0bd23
```

**Notes:**

- `--resume` restores the latest saved snapshot: the ADK session, sandbox metadata, and the resolved runtime config.
- `--resume-from` accepts a saved-session directory name, a bare session-id suffix, or an absolute path to a saved-session directory. It implies `--resume`.
- `--resume` reuses `agent_dir` from the saved snapshot metadata. For older snapshots that lack this field, pass `--agent` explicitly.
- For legacy snapshots that lack `resolved_config.toml`, pass `--config` explicitly.

### Logging

| Flag | Purpose | Default |
|------|---------|---------|
| `--log_level` | Terminal log verbosity. One of `debug`, `info`, `warning`, `error`, `critical` | `info` |

---

For the step-by-step runtime workflow (session creation, sandbox initialization, ADK service wiring, request flow, and cleanup), see [OpenSage Workflow](../inside-opensage/workflow.md), which uses `opensage web` as its worked example.

For component-level architecture, see [Components](../inside-opensage/components/index.md) and the [Roadmap](../inside-opensage/roadmap.md).
