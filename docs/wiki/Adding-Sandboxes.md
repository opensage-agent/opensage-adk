# Adding a New Sandbox Type

## Overview

Sandboxes provide isolated execution environments. You can add custom sandbox types with specific initialization logic.

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
