# `[sandbox]` Reference

Field reference for the `[sandbox]` section. For how to use it, see the [Sandbox configuration guide](../../get-started/customize/sandbox.md).

## Top-Level Sandbox Settings

Fields directly under `[sandbox]`.

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `default_image` | `string` | Default Docker image for sandboxes | `None` |
| `backend` | `string` | Sandbox backend type (see below) | `"native"` |
| `project_relative_shared_data_path` | `string` | Path relative to project root for shared data (mounted as `/shared` in containers) | `None` |
| `absolute_shared_data_path` | `string` | Absolute path for shared data | `None` |
| `host_shared_mem_dir` | `string` | Absolute host path mounted into all sandboxes as `/mem/shared` | `None` |
| `mount_host_paths` | `list[string]` | Global host bind mounts injected into all sandboxes. Format: `"/abs/host:/abs/container[:ro\|rw]"` | `[]` |
| `tolerations` | `list[dict]` | Kubernetes tolerations applied to all pods (k8s backend only) | `None` |

## Supported Backends

| Backend | Status | Description |
|---------|--------|-------------|
| `native` | Stable | Local Docker backend |
| `remotedocker` | Under development | Remote Docker daemon over SSH/TCP |
| `opensandbox` | Under development | OpenSandbox-managed execution backend |
| `agentdocker-lite` | Under development | Namespace-based lightweight local isolation |
| `local` | Under development | Direct host execution without containers |
| `k8s` | Under development | Kubernetes backend |

## Per-Sandbox Configuration

Each sandbox is configured under `[sandbox.sandboxes.<name>]`. Common built-in names:

| Name | Purpose |
|------|---------|
| `main` | Primary analysis sandbox |
| `joern` | Joern static analysis sandbox |
| `codeql` | CodeQL analysis sandbox |
| `neo4j` | Neo4j database container |
| `gdb_mcp` | GDB debugger MCP service |
| `pdb_mcp` | PDB debugger MCP service |
| `fuzz` | Fuzzing environment |

### Container Fields

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `image` | `string` | Docker image name/tag | `None` |
| `container_id` | `string` | Connect to existing container (instead of creating new) | `None` |
| `timeout` | `integer` | Container operation timeout in seconds | `300` |
| `project_relative_dockerfile_path` | `string` | Path to Dockerfile relative to project root | `None` |
| `absolute_dockerfile_path` | `string` | Absolute path to Dockerfile | `None` |
| `command` | `string` | Override container command (empty string = use Dockerfile default, `None` = use `bash`) | `None` |
| `platform` | `string` | Platform architecture (e.g., `"linux/amd64"`) | `None` |
| `network` | `string` | Docker network name | `None` |
| `host_gateway` | `boolean` | Add `host.docker.internal:host-gateway` to this Docker container so it can reach services on the Docker host | `false` |
| `privileged` | `boolean` | Run container in privileged mode | `false` |
| `security_opt` | `list[string]` | Security options | `[]` |
| `cap_add` | `list[string]` | Additional capabilities | `[]` |
| `gpus` | `string` | GPU allocation (e.g., `"all"` or `"device=GPU-UUID"`) | `None` |
| `shm_size` | `string` | Shared memory size (e.g., `"2g"`) | `None` |
| `mem_limit` | `string` | Memory limit (e.g., `"4g"`) | `None` |
| `cpus` | `string` | CPU limit (e.g., `"2"`) | `None` |
| `user` | `string` | User to run as (e.g., `"1000:1000"`) | `None` |
| `working_dir` | `string` | Working directory in container | `None` |

### Build Fields

| Field | Type | Description |
|-------|------|-------------|
| `build_args` | `dict[string, string]` | Docker build arguments |
| `using_cached` | `boolean` | Whether to use cached image (internal flag) |

### Environment, Volumes, and Ports

| Field | Type | Description |
|-------|------|-------------|
| `environment` | `dict[string, any]` | Environment variables |
| `volumes` | `list[string]` | Volume mounts in format `"/host:/container:ro"` |
| `mounts` | `list[string]` | Docker mount specifications |
| `ports` | `dict[string, int\|string]` | Port mappings in format `{"port/tcp" = host_port}` |
| `docker_args` | `list[string]` | Raw arguments passed through to Docker CLI |
| `extra` | `dict[string, any]` | Additional custom configuration (e.g., `initializer_timeout_sec`) |

### Kubernetes-Specific Fields

| Field | Type | Description |
|-------|------|-------------|
| `pod_name` | `string` | Connect to existing Pod instead of creating new |
| `container_name` | `string` | Name of container within the Pod |

!!! note "`host_gateway` compatibility"
    On Linux, Docker's `host-gateway` option requires Docker Engine 20.10 or newer. Older Docker daemons may fail container startup when `host_gateway = true`. The option has no effect when the same sandbox also sets `network = "host"`.
