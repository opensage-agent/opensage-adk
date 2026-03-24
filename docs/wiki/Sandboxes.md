# Sandbox System Guide

## Overview

The OpenSage sandbox system provides isolated execution environments through a
pluggable backend architecture. This guide covers:

- **Sandbox Backends**: Execution engines (`native`; `remotedocker`,
  `opensandbox`, `agentdocker-lite`, `local`, and `k8s` are all currently under
  development)
- **Sandbox Initializers**: Functional types (main, neo4j, joern, gdb_mcp, etc.)

## Sandbox Backends

Backends determine **where and how** containers are executed.

### Available Backends

| Backend | Description | Use Case |
|---------|-------------|----------|
| **native** | Local Docker daemon | Development, testing |
| **remotedocker** | Remote Docker via SSH/TCP (**under development**) | Under development |
| **opensandbox** | OpenSandbox-managed remote execution backend (**under development**) | Under development |
| **agentdocker-lite** | Namespace sandbox backend built on `agentdocker-lite` (**under development**) | Under development |
| **local** | No containers (direct execution on the host; **under development**) | Under development |
| **k8s** | Kubernetes cluster backend (**under development**) | Under development |

### Selecting a Backend

In configuration file:

```toml
[sandbox]
backend = "native"  # other backend values are currently under development
```

### Native Docker Backend

The default backend. Sandboxes run as local Docker containers on the current
machine.

Typical use cases:

- Local development
- Integration tests
- Standard single-machine execution

### Remote Docker Backend

The `remotedocker` backend is currently **under development**.

Execute sandboxes on remote Docker daemons (e.g., GPU servers, cloud VMs).

#### Prerequisites

**Local Machine**:

- SSH client

**Remote Machine**:

- Docker Engine 20.10+
- SSH server
- User in docker group

#### SSH Setup

1. **Generate SSH key** (if needed):
   ```bash
   ssh-keygen -t ed25519
   ```

2. **Copy key to remote**:
   ```bash
   ssh-copy-id username@remote-host
   ```

3. **Configure SSH** (`~/.ssh/config`):
   ```
   Host my-remote-server
       HostName remote-host.example.com
       User username
       IdentityFile ~/.ssh/id_ed25519
   ```

4. **Verify**:
   ```bash
   ssh my-remote-server "docker ps"
   ```

#### Configuration

```toml
[sandbox]
backend = "remotedocker"
docker_host = "ssh://my-remote-server"
docker_remote_host = "192.0.2.100"  # optional, auto-parsed if not set

[sandbox.sandboxes.main]
image = "ubuntu:22.04"
# Same as native backend
```

#### How It Works

- **Image operations**: Build/pull on remote Docker
- **Volume creation**: Data transferred via Docker API (put_archive)
- **Port allocation**: Dynamic (Docker assigns random ports)
- **Service access**: Local connects to `remote_host:dynamic_port`
- **Container execution**: All containers run on remote host

#### Differences from Native Backend

| Feature | Native | Remote Docker |
|---------|--------|---------------|
| Container location | Local machine | Remote machine |
| Volume creation | Instant (bind mount) | Slower (data upload) |
| Image build | Local | Remote (with context upload) |
| Port allocation | Loopback IP (127.0.0.x) | Dynamic ports |
| Concurrent tasks | ~250 (IP limit) | 1000+ (port-based) |

### OpenSandbox Backend

The `opensandbox` backend is currently **under development**.

Use OpenSandbox as the execution provider while keeping the same OpenSage
sandbox abstractions.

This backend is selected with:

```toml
[sandbox]
backend = "opensandbox"
```

Unlike `remotedocker`, this backend relies on `sandbox.opensandbox` provider
configuration and delegates sandbox lifecycle to OpenSandbox services.

Typical use cases:

- Managed remote execution
- Hosted sandbox infrastructure
- Teams already using OpenSandbox APIs

### AgentDocker-Lite Backend

The `agentdocker-lite` backend is currently **under development**.

`agentdocker-lite` is a lightweight local isolation backend built on Linux
namespaces/cgroups rather than a full Docker daemon.

This backend is selected with:

```toml
[sandbox]
backend = "agentdocker-lite"
```

Typical use cases:

- Lightweight local isolation
- Experiments where Docker is undesirable
- Advanced namespace-based setups

### Local Backend

The `local` backend is currently **under development**.

The `local` backend executes commands directly on the host machine with no
container runtime. It is mainly for debugging and simple development loops.

Important limitations:

- No shared volumes
- Only one sandbox is supported
- No real container isolation

### Kubernetes Backend

The `k8s` backend exists in the codebase, but it should still be treated as
**under development** in current documentation and user guidance.

## Extending the sandbox system

- [Adding a sandbox](Adding-a-Sandbox.md): Add a new sandbox **type** by writing a
  sandbox initializer and registering it.
- [Adding a new sandbox backend](Adding-a-New-Sandbox-Backend.md): Add a new
  execution backend (e.g., a new container/runtime environment).

## See Also

[Core Components](Core-Components.md) - Sandbox system details
[Development Guides](Development-Guides.md) - Other development guides
