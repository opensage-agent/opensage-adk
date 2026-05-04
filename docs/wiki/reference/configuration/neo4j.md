# `[neo4j]` Reference

Field reference for the `[neo4j]` section. For how to use it, see the [Neo4j configuration guide](../../get-started/customize/neo4j.md).

## Fields

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `user` | `string` | Neo4j username | `None` |
| `password` | `string` | Neo4j password | `None` |
| `bolt_port` | `integer` | Neo4j Bolt protocol port | `7687` |
| `neo4j_http_port` | `integer` | Neo4j HTTP port | `7474` |

!!! note "URI is constructed dynamically"
    The `uri` property is built at runtime as `neo4j://{default_host}:{bolt_port}`. If `default_host` (root-level field) is not set, it falls back to `127.0.0.1`.
