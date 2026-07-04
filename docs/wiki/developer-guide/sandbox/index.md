---
icon: lucide/container
---

# Customizing Sandbox

## Overview

The OpenSage-ADK sandbox system provides isolated execution environments through a pluggable backend architecture. It is built on two independent concepts:

- **Sandbox Backends** are execution engines that manage where and how containers run (`native`; `podman`, `remotedocker`, `opensandbox`, `nitrobox`, `local`, and `k8s` are all currently under development)
- **Sandbox Initializers** are functional types that define what gets installed inside a container (main, neo4j, joern, gdb_mcp, etc.)

For the TOML configuration reference (fields, backends, per-sandbox settings), see [Sandbox Configuration](../../reference/configuration/sandbox.md).

## Sandbox Backends

Backends determine **where and how** containers are executed.

### Available Backends

| Backend | Description | Use Case |
|---------|-------------|----------|
| **native** | Local Docker daemon | Development, testing |
| **podman** | Local Podman with Docker-compatible API socket (**under development**) | Podman-based local execution |
| **remotedocker** | Remote Docker via SSH/TCP (**under development**) | Under development |
| **opensandbox** | OpenSandbox-managed remote execution backend (**under development**) | Under development |
| **nitrobox** | Namespace sandbox backend built on `nitrobox` (**under development**) | Under development |
| **local** | No containers (direct execution on the host; **under development**) | Under development |
| **k8s** | Kubernetes cluster backend (**under development**) | Under development |

### Selecting a Backend

```toml
[sandbox]
backend = "native"  # other backend values are currently under development
```

### Native Docker Backend

The default backend. Sandboxes run as local Docker containers on the current machine. Typical use cases: local development, integration tests, and standard single-machine execution.

### Podman Backend

The `podman` backend uses the local `podman` CLI for image and volume operations, and Podman's Docker-compatible API socket for container exec and file archive operations. Start the socket with `systemctl --user enable --now podman.socket`, or set `PODMAN_DOCKER_HOST` to the socket URL.

### Remote Docker Backend

The `remotedocker` backend is currently **under development**. It executes sandboxes on remote Docker daemons (e.g., GPU servers, cloud VMs).

**Prerequisites:**

| Machine | Requirements |
|---------|-------------|
| Local | SSH client |
| Remote | Docker Engine 20.10+, SSH server, user in docker group |

**SSH setup:**

1. Generate SSH key (if needed): `ssh-keygen -t ed25519`
2. Copy key to remote: `ssh-copy-id username@remote-host`
3. Configure SSH (`~/.ssh/config`):
   ```
   Host my-remote-server
       HostName remote-host.example.com
       User username
       IdentityFile ~/.ssh/id_ed25519
   ```
4. Verify: `ssh my-remote-server "docker ps"`

**Configuration:**

```toml
[sandbox]
backend = "remotedocker"
docker_host = "ssh://my-remote-server"
docker_remote_host = "192.0.2.100"  # optional, auto-parsed if not set

[sandbox.sandboxes.main]
image = "ubuntu:22.04"
```

**How it works:** Image operations (build/pull) happen on the remote Docker. Volumes are transferred via Docker API (`put_archive`). Port allocation is dynamic (Docker assigns random ports). The local machine connects to `remote_host:dynamic_port`.

**Differences from native backend:**

| Feature | Native | Remote Docker |
|---------|--------|---------------|
| Container location | Local machine | Remote machine |
| Volume creation | Instant (bind mount) | Slower (data upload) |
| Image build | Local | Remote (with context upload) |
| Port allocation | Loopback IP (127.0.0.x) | Dynamic ports |
| Concurrent tasks | ~250 (IP limit) | 1000+ (port-based) |

### OpenSandbox Backend

The `opensandbox` backend is currently **under development**. It uses OpenSandbox as the execution provider while keeping the same OpenSage-ADK sandbox abstractions. Unlike `remotedocker`, this backend relies on `sandbox.opensandbox` provider configuration and delegates sandbox lifecycle to OpenSandbox services.

```toml
[sandbox]
backend = "opensandbox"
```

### Nitrobox Backend

The `nitrobox` backend is currently **under development**. It is a lightweight local isolation backend built on Linux namespaces/cgroups rather than a full Docker daemon.

```toml
[sandbox]
backend = "nitrobox"
```

### Local Backend

The `local` backend is currently **under development**. It executes commands directly on the host machine with no container runtime. Mainly for debugging and simple development loops. Limitations: no shared volumes, only one sandbox supported, no real container isolation.

### Kubernetes Backend

The `k8s` backend exists in the codebase, but should still be treated as **under development**.

## Extending the Sandbox System

- [Adding a sandbox](adding-a-sandbox.md): Add a new sandbox **type** by writing a sandbox initializer and registering it.
- [Adding a new sandbox backend](adding-a-new-sandbox-backend.md): Add a new execution backend (e.g., a new container/runtime environment).
