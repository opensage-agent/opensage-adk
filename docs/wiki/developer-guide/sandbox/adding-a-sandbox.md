# Adding a sandbox

## Overview

In this project, a “sandbox” is created by combining:

- A **sandbox backend** (where/how the environment runs), and
- A **sandbox initializer** (what gets installed/configured in that environment).

This guide covers adding a **new sandbox type** by implementing and registering a sandbox initializer.

## Steps

### 1) Create an initializer

Add a new initializer under:

- `opensage-adk/src/opensage/sandbox/initializers/`

Implement the `SandboxInitializer` interface from `opensage-adk/src/opensage/sandbox/initializers/base.py`.

### 2) Let the factory discover it

Initializers are discovered automatically by `opensage.sandbox.factory`:

- Built-in initializers: `opensage-adk/src/opensage/sandbox/initializers/*.py`
- User initializers: `~/.local/opensage/initializers/*.py`

`SANDBOX_INITIALIZERS = _discover_initializers()` scans those directories at import time. The sandbox type name comes from the file stem, so `my_sandbox.py` is discoverable as `"my_sandbox"`. Keep exactly one `SandboxInitializer` subclass per file.

### 3) Add configuration

Add any required config fields to:

- `opensage-adk/src/opensage/config/config_dataclass.py`

and update the default config template (if you ship one) under:

- `opensage-adk/src/opensage/templates/configs/`

### 4) Configure your sandbox in TOML

Example:

```toml
[sandbox.sandboxes.my_sandbox]
image = "my_image:tag"
```

## Python Dependencies in Sandbox Images

If your initializer or tools need Python packages, install them in the **sandbox Docker image** (not at runtime inside a running container).

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

Note: sandbox command execution is **non-persistent** (each command is a fresh process). Do not rely on `source /app/.venv/bin/activate` carrying over between commands. Prefer `/app/.venv/bin/python ...`.

## Example

```python
from opensage.sandbox.base_sandbox import BaseSandbox
from opensage.sandbox.initializers.base import SandboxInitializer


class MySandboxInitializer(SandboxInitializer):
    async def _async_initialize_impl(
        self: BaseSandbox, all_sandboxes: dict[str, BaseSandbox]
    ) -> bool:
        # Install or configure resources needed by this sandbox type.
        return True
```

## Initialization Flow

1. Sandbox container is created
2. The backend calls `async_initialize(all_sandboxes)`
3. Inside that wrapper, the base class calls your `_async_initialize_impl(all_sandboxes)`, then runs `ensure_ready()` as its final step
4. Sandbox is ready for use

Override `_async_initialize_impl`, not `async_initialize`; overriding the wrapper skips the built-in `ensure_ready()` and error handling.

## Skill Dependency Installers

Skills under `bash_tools/` can ship optional dependency installers:

- `deps/<sandbox_type>/install.sh` (sandbox-specific), and/or
- `deps/install.sh` (generic)

The execution location is declared in `SKILL.md` YAML frontmatter via `should_run_in_sandbox`. During sandbox initialization, enabled skill installers are executed best-effort and skipped on subsequent runs via a marker under `/shared`.
