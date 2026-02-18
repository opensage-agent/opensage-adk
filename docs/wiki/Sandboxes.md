# Sandbox System Guide

## Overview

The OpenSage sandbox system provides isolated execution environments through a pluggable backend architecture. This guide covers:

- **Sandbox Backends**: Execution engines (Native Docker, Remote Docker, Kubernetes under development)
- **Sandbox Initializers**: Functional types (main, neo4j, joern, gdb_mcp, etc.)

## Sandbox Backends

Backends determine **where and how** containers are executed.

### Available Backends

| Backend | Description | Use Case |
|---------|-------------|----------|
| **native** | Local Docker daemon | Development, testing |
| **remotedocker** | Remote Docker via SSH/TCP | Remote execution, GPU servers |
| **k8s** | Kubernetes cluster (under development) | Under development |
| **local** | No containers (direct execution) | Debugging |

### Selecting a Backend

In configuration file:

```toml
[sandbox]
backend = "native"  # or "remotedocker", "k8s", "local"
```

### Remote Docker Backend

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

## Extending the sandbox system

- [Adding a sandbox](Adding-a-Sandbox.md): Add a new sandbox **type** by writing a
  sandbox initializer and registering it.
- [Adding a new sandbox backend](Adding-a-New-Sandbox-Backend.md): Add a new
  execution backend (e.g., a new container/runtime environment).

## See Also

[Core Components](Core-Components.md) - Sandbox system details
[Development Guides](Development-Guides.md) - Other development guides
