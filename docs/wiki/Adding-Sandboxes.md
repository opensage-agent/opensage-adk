# Sandbox System Guide

## Overview

The AIgiSE sandbox system provides isolated execution environments through a pluggable backend architecture. This guide covers:

- **Sandbox Backends**: Execution engines (Native Docker, Remote Docker, Kubernetes)
- **Sandbox Initializers**: Functional types (main, neo4j, joern, gdb_mcp, etc.)

## Sandbox Backends

Backends determine **where and how** containers are executed.

### Available Backends

| Backend | Description | Use Case |
|---------|-------------|----------|
| **native** | Local Docker daemon | Development, testing |
| **remotedocker** | Remote Docker via SSH/TCP | Remote execution, GPU servers |
| **k8s** | Kubernetes cluster | Production, multi-node |
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

## Adding a New Sandbox Initializer

Initializers add specific functionality to sandboxes (e.g., Neo4j, Joern, debugging tools).

## Steps

1. Create initializer in `src/<package>/sandbox/initializers/`
2. Implement `SandboxInitializer` interface
3. Add configuration in `config_dataclass.py`
4. Update default config template

## Python Dependencies in Sandbox Images

If your sandbox initializer or tools need Python packages, install them in the
**sandbox Docker image** (not at runtime via `pip install` inside the running
container).

Recommended pattern (used by `main`, `joern`, `gdb_mcp`, `pdb_mcp`):

1. Install `uv` in the Dockerfile:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

2. Create a venv under `/app`:

```bash
uv venv --python 3.12
```

3. Install Python deps into the venv:

```bash
uv pip install <deps...>
```

Note: sandbox command execution is **non-persistent** (each command is a fresh
process). Do not rely on `source /app/.venv/bin/activate` carrying over between
commands. Prefer `/app/.venv/bin/python ...`.

## Example

```python
# src/<package>/sandbox/initializers/my_sandbox.py
from .base import SandboxInitializer

class MySandboxInitializer(SandboxInitializer):
    async def async_initialize(self) -> None:
        # Initialize sandbox-specific resources
        # Access session via self if needed
        pass
```

## Configuration

Add sandbox configuration in TOML:

```toml
[sandbox.sandboxes.my_sandbox]
image = "my_image:tag"
# ... other config options
```

## Initialization Flow

1. Sandbox container is created
2. `async_initialize()` is called
3. Resources are set up
4. Sandbox is ready for use

## See Also

- [Core Components](Core-Components.md) - Sandbox system details
- [Core Concepts](Core-Concepts.md) - Sandbox lifecycle
- [Development Guides](Development-Guides.md) - Other development guides
