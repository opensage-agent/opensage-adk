# opensage web

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
  --reload / --no-reload          Whether to enable auto reload.  [default:
                                  reload]
  --log_level [debug|info|warning|error|critical]
                                  Logging level for the server.  [default:
                                  INFO]
  --neo4j_logging / --no-neo4j_logging
                                  Enable Neo4j event logging via monkey
                                  patches.  [default: no-neo4j_logging]
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
