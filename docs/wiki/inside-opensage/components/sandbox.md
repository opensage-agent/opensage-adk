# Sandboxes

A **sandbox** is an isolated execution environment (a Docker container, a Kubernetes pod, or a native process) where an agent's tool calls actually run. The goal is a hard boundary: the LLM can propose any command, but execution stays confined to a sandbox with its own filesystem, network, and resource limits.

Source lives under [`src/opensage/sandbox/`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/sandbox).

## Two Orthogonal Axes

The sandbox system deliberately separates two concerns:

- **Backend**: *where and how* a container runs. One of `native` (local Docker), `remotedocker`, `opensandbox`, `agentdocker-lite`, `local` (no container), or `k8s`. Each backend is a subclass of `BaseSandbox` (`base_sandbox.py`) implementing container / pod lifecycle.
- **Initializer**: *what gets installed inside*. A built-in set under `sandbox/initializers/` configures a particular sandbox type (`main`, `joern`, `codeql`, `neo4j`, `gdb_mcp`, `pdb_mcp`, `fuzz`) by running setup commands after the container is up.

Because these axes are independent, the **same sandbox type runs identically on every backend**, and you can swap backends (local dev -> remote Docker -> k8s in CI) without touching the per-sandbox setup code.

## Many Sandboxes per Session

A single session can own **many sandboxes at once**. For example, a PoC generation agent typically launches:

| Sandbox | Purpose |
|---|---|
| `main` | Where the agent edits source, runs builds, drops PoC inputs. |
| `neo4j` | A Neo4j container serving the code-property graph for static analysis. |
| `gdb_mcp` | A sidecar running the GDB MCP server; exposed on `sse_port = 1111`. |
| `joern` | Joern for CPG generation. |

The `[sandbox.sandboxes.<name>]` section in your `config.toml` declares each one. Tools pick their sandbox by name, so a GDB tool knows to execute against `gdb_mcp`, not `main`.

## Lifecycle

```python
# During session startup
session.sandboxes.initialize_shared_volumes()
await session.sandboxes.launch_all_sandboxes()
await session.sandboxes.initialize_all_sandboxes(continue_on_error=True)
```

1. **Shared volumes created.** The `/shared` bind mount is populated with the project's code / data.
2. **Containers launched.** All sandboxes referenced by the agent (after [pruning](./sessions.md#sandbox-pruning-via-a-dummy-agent)) are started in parallel.
3. **Initializers run.** Each container runs its initializer (apt installs, venv creation, MCP-service start-up, and so on).
4. **MCP readiness polled.** Sandboxes hosting an MCP service (`gdb_mcp`, `pdb_mcp`, custom) are not marked `READY` until `_check_mcp_connections()` successfully pings the SSE endpoint.
5. **Ready.** The agent can now use tools that target this sandbox.

`continue_on_error=True` is the default; if one sandbox fails to initialize, the others still come up. Makes debugging easier, at the cost of the agent having to discover missing capabilities lazily.

## Sandbox Dependencies

Tools declare which sandbox(es) they need via `@requires_sandbox(...)`:

```python
from opensage.toolbox.sandbox_requirements import requires_sandbox

@requires_sandbox("gdb_mcp", "main")
def step_into(tool_context, ...):
    ...
```

Skills declare the same thing in their `SKILL.md`:

```markdown title="SKILL.md"
## Requires Sandbox

- `neo4j`
- `joern`
```

`collect_sandbox_dependencies()` walks the agent tree at session startup, gathers every declared dependency, and prunes the sandbox list to just those. `main` is always included.

## Shared Storage

Two shared mount points that are present in *every* sandbox by default:

- **`/shared`**: populated from the session's shared-data path (`project_relative_shared_data_path` or `absolute_shared_data_path` in `[sandbox]`). This is where target code, PoC inputs, and submission scripts live.
- **`/mem/shared`**: populated from the host path in `sandbox.host_shared_mem_dir`. File-based shared memory used by tools that want to leave breadcrumbs visible to every sandbox.

Per-sandbox mounts (`volumes`, `mounts`) are additive on top of these.

## Cleanup

On shutdown:

1. Containers are stopped (backend-dependent).
2. If `auto_cleanup = true` in the root config, shared volumes and per-sandbox volumes are deleted.
3. Networks / namespaces created for this session are removed.

## Related References

- [`[sandbox]` configuration reference](../../reference/configuration/sandbox.md): every field.
- [Sandbox system guide](../../developer-guide/sandbox/index.md): internals, backend implementation.
- [Adding a Sandbox](../../developer-guide/sandbox/adding-a-sandbox.md) and [Adding a Sandbox Backend](../../developer-guide/sandbox/adding-a-new-sandbox-backend.md): extension points.
