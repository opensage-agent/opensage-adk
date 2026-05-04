# Neo4j

**Section:** `[neo4j]`

Configures the Neo4j graph database connection. This section is only read when the agent uses `memory_management=MemoryManagement.DATABASE` for long-term graph memory, or when a plugin such as the Neo4j history logger needs a graph backend.

## Example

```toml title="config.toml"
[neo4j]
user = "neo4j"
password = "callgraphn4j!"
bolt_port = 7687
neo4j_http_port = 7474
```

!!! note "URI is constructed dynamically"
    The `uri` property is built at runtime as `neo4j://{default_host}:{bolt_port}`. If `default_host` (the root-level field) is not set, it falls back to `127.0.0.1`. Do not write the full URI in the config; set the host once at the root and each subsystem composes its own.

The Neo4j container itself is usually declared under `[sandbox.sandboxes.neo4j]` in the same file so OpenSage can launch it on demand. See the [Agent with Neo4j Logging](../examples/agents_with_features/sample_neo4j_logging.md) example for a full setup.

---

See the [`[neo4j]` field reference](../../reference/configuration/neo4j.md) for all fields.
