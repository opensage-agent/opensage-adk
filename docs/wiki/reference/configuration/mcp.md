# `[mcp]` Reference

Field reference for the `[mcp]` section. For how to use it, see the [MCP configuration guide](../../get-started/customize/mcp.md).

MCP services are configured under `[mcp.services.<service_name>]`.

## Built-In Service Names

| Service | Purpose |
|---------|---------|
| `gdb_mcp` | GDB debugger MCP service |
| `pdb_mcp` | PDB debugger MCP service |

Any additional `<service_name>` is also accepted; OpenSage does not restrict the set.

## Per-Service Fields

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `sse_port` | `integer` | Server-Sent Events (SSE) server port | Required |
| `sse_host` | `string` | SSE server host. If `None`, uses `default_host` from root config | `None` |
