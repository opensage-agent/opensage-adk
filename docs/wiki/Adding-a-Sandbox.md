# Adding a sandbox

## Overview

In this project, a “sandbox” is created by combining:

- A **sandbox backend** (where/how the environment runs), and
- A **sandbox initializer** (what gets installed/configured in that environment).

This guide covers adding a **new sandbox type** by implementing and registering
a sandbox initializer.

## Steps

### 1) Create an initializer

Add a new initializer under:

- `OpenSage/src/opensage/sandbox/initializers/`

Implement the `SandboxInitializer` interface from
`OpenSage/src/opensage/sandbox/initializers/base.py`.

### 2) Register the initializer

Register your initializer in the initializer registry:

- `OpenSage/src/opensage/sandbox/factory.py` (`SANDBOX_INITIALIZERS`)

This makes the sandbox type discoverable by name (e.g. `"my_sandbox"`).

### 3) Add configuration

Add any required config fields to:

- `OpenSage/src/opensage/config/config_dataclass.py`

and update the default config template (if you ship one) under:

- `OpenSage/src/opensage/templates/configs/`

### 4) Configure your sandbox in TOML

Example:

```toml
[sandbox.sandboxes.my_sandbox]
image = "my_image:tag"
```

## Python dependencies in sandbox images

If your initializer or tools need Python packages, install them in the **sandbox
Docker image** (not at runtime inside a running container).

Recommended pattern:

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
from opensage.sandbox.initializers.base import SandboxInitializer


class MySandboxInitializer(SandboxInitializer):
  async def async_initialize(self) -> None:
    pass
```

## Initialization flow

1. Sandbox container is created
2. `async_initialize()` is called
3. Resources are set up
4. Sandbox is ready for use

## Skill dependency installers

Skills under `bash_tools/` can ship optional dependency installers:

- `deps/<sandbox_type>/install.sh` (sandbox-specific), and/or
- `deps/install.sh` (generic)

The execution location is declared in `SKILL.md` YAML frontmatter via
`should_run_in_sandbox`. During sandbox initialization, enabled skill installers
are executed best-effort and skipped on subsequent runs via a marker under
`/shared`.
