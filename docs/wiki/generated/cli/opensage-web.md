# opensage web

```text
Usage: opensage web [OPTIONS]

  Starts an OpenSage-flavored Web UI: prepare environment then serve agents.

Options:
  --config FILE                   Path to OpenSage TOML config.  [required]
  --agent DIRECTORY               Path to the agent folder (must contain agent
                                  files).  [required]
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
  --help                          Show this message and exit.
```
