# Neo4j

**Section:** `[neo4j]`

Configures the Neo4j graph database connection. OpenSage reads this section when a tool or sandbox needs a graph backend, for example the Joern code-property-graph static-analysis stack (`neo4j_query`, `search_function`) or a Neo4j history logger.

## Example

```toml title="config.toml"
[neo4j]
user = "neo4j"
password = "callgraphn4j!"
bolt_port = 7687
neo4j_http_port = 7474
```

!!! note "URI is constructed dynamically"
    The `uri` property is built at runtime as `bolt://{default_host}:{bolt_port}`. If `default_host` (the root-level field) is not set, it falls back to `127.0.0.1`. Set the host once at the root and let each subsystem compose its own URI, rather than writing the full URI in the config.

The Neo4j container itself is usually declared under `[sandbox.sandboxes.neo4j]` in the same file so OpenSage can launch it on demand. See the [Agent with Neo4j Logging](../examples/agents_with_features/sample_neo4j_logging.md) example for a full setup.

---

See the [`[neo4j]` field reference](../../reference/configuration/neo4j.md) for all fields.
