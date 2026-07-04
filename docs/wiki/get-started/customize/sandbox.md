# Sandbox

**Section:** `[sandbox]`

Every tool call runs inside a **sandbox**: a container or native process that isolates the agent's actions from your host. The default sandbox (`backend = "native"`, image `ubuntu:24.04`) is fine for hello-world agents, but real workloads usually pin to a custom image. Every agent has at least a `main` sandbox; more complex agents add sidecars (`joern`, `neo4j`, `gdb_mcp`, and similar) alongside it.

## Minimal Example

```toml title="config.toml"
[sandbox]
backend = "native"

[sandbox.sandboxes.main]
image = "ubuntu:24.04"
project_relative_dockerfile_path = "dockerfiles/main/Dockerfile"
timeout = 300
```

`backend` picks how sandboxes are implemented (local Docker, remote Docker, Kubernetes, and so on). `native` (the default) runs local Docker and is the right choice for laptops and most CI runners. Other backends exist in the code but are under active development; see [Backend notes](#backend-notes) below for caveats.

## Multiple Sandboxes

Each named block under `[sandbox.sandboxes.<name>]` is an independent container with its own image, build args, environment, mounts, and ports. Tools request a sandbox by name.

```toml title="config.toml"
[sandbox]
backend = "native"
project_relative_shared_data_path = "data/my_project.tar.gz"
mount_host_paths = [
  "/data/datasets:/workspace/datasets:ro",
  "/tmp/run-cache:/workspace/run-cache:rw",
]

[sandbox.sandboxes.main]
image = "ubuntu:20.04"
project_relative_dockerfile_path = "dockerfiles/main/Dockerfile"
timeout = 300
network = "agent_net"

[sandbox.sandboxes.main.build_args]
BASE_IMAGE = "ubuntu:20.04"

[sandbox.sandboxes.main.environment]
PYTHONPATH = "/shared/code"

[sandbox.sandboxes.main.ports]
"8080/tcp" = 8080

[sandbox.sandboxes.joern]
image = "opensage/joern"
project_relative_dockerfile_path = "dockerfiles/joern/Dockerfile"
command = ""

[sandbox.sandboxes.joern.environment]
JAVA_OPTS = "-Xmx16G -Xms4G"

[sandbox.sandboxes.joern.ports]
"8081/tcp" = 18087
```

!!! info "Mount semantics"
    `mount_host_paths` is appended to every sandbox's `volumes`. Absolute source paths are treated as host mount sources; mode defaults to `rw` when omitted.

    `project_relative_shared_data_path` and `absolute_shared_data_path` seed the shared data volume mounted at `/shared`. Use `mount_host_paths` for additional host directories that should appear inside every sandbox.

!!! tip "Docker networks"
    Omit `network` unless the sandbox should join a specific Docker network. For example, after creating a network named `agent_net`, set `network = "agent_net"` on the sandbox that should connect to it.

## Backend Notes

- **`native`**: default; recommended for local development.
- **`remotedocker`**: under development; additionally uses `docker_host` / `docker_remote_host`.
- **`opensandbox`**: under development; additionally requires `sandbox.opensandbox` provider settings.
- **`nitrobox`**: under development; commonly uses `sandbox.sandboxes.<name>.extra` for namespace/cgroup options such as `fs_backend`, `cpu_max`, or `memory_max`.
- **`local`**: under development, mainly for debugging; supports only a single sandbox, no shared volumes.
- **`k8s`**: exists in code, still under development.

## Sandbox Images

OpenSage provides some sandboxes shipping with Python tooling preinstalled. In the default template (`src/opensage/templates/configs/default_config.toml`):

| Sandbox | Dockerfile | Python packages | Notes |
|---------|-----------|-----------------|-------|
| `main` | `src/opensage/templates/dockerfiles/main/Dockerfile` | `neo4j` | Provides `python3` via `/app/.venv/bin/python` |
| `joern` | `src/opensage/templates/dockerfiles/joern/Dockerfile` | `httpx`, `websockets` | Used by Joern query helper scripts |

These images install Python deps with `uv` in the Dockerfile (create `/app/.venv`, run `uv pip install …`) rather than at runtime inside a running container.

---

See the [`[sandbox]` field reference](../../reference/configuration/sandbox.md) for the full list of top-level fields, backends, per-sandbox container/build/environment/k8s fields.
