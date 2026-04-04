# Adding a sandbox

## Overview

In this project, a “sandbox” is created by combining:

- A **sandbox backend** (where/how the environment runs), and
- A **sandbox initializer** (what gets installed/configured in that environment).

This guide covers adding a **new sandbox type** by implementing a sandbox
initializer.

## Steps

### 1) Create an initializer

Create a `.py` file containing a single `SandboxInitializer` subclass. Place it
in either of the following directories:

- **Built-in** (framework contributors): `src/opensage/sandbox/initializers/`
- **User-defined** (no source changes needed): `~/.local/opensage/initializers/`

The **file name** (without `.py`) becomes the sandbox type name used in
configuration. For example, `my_sandbox.py` registers as `”my_sandbox”`.

User-defined initializers with the same file name as a built-in one will
override it.

Implement the `SandboxInitializer` interface from
`opensage.sandbox.initializers.base`. The only method you need to override is
`_async_initialize_impl`.

### 2) No registration needed

Initializers are **auto-discovered** by scanning the directories above at
startup. There is no need to manually edit `factory.py` or any registry.

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

Create `~/.local/opensage/initializers/my_sandbox.py` (or add to the built-in
directory):

```python
from opensage.sandbox.base_sandbox import BaseSandbox
from opensage.sandbox.initializers.base import SandboxInitializer


class MySandboxInitializer(SandboxInitializer):
    async def _async_initialize_impl(
        self: BaseSandbox, all_sandboxes: dict[str, BaseSandbox]
    ) -> bool:
        # Run setup commands inside the container
        msg, err = self.run_command_in_container(
            ["bash", "/sandbox_scripts/my_setup.sh"], timeout=600
        )
        if err != 0:
            return False

        # Optional: wait for another sandbox to be ready
        # if not await all_sandboxes["neo4j"].wait_for_ready_or_error():
        #     return False

        return True
```

Then reference it in your config:

```toml
[sandbox.sandboxes.my_sandbox]
image = "my_image:tag"
```

## Initialization flow

1. Sandbox container is created
2. `_async_initialize_impl()` is called (return `True` on success, `False` on failure)
3. `_ensure_ready_impl()` is called (override to wait for MCP services, etc.)
4. Sandbox is ready for use

## Skill dependency installers

Skills under `bash_tools/` can ship optional dependency installers:

- `deps/<sandbox_type>/install.sh` (sandbox-specific), and/or
- `deps/install.sh` (generic)

The execution location is declared in `SKILL.md` YAML frontmatter via
`should_run_in_sandbox`. During sandbox initialization, enabled skill installers
are executed best-effort and skipped on subsequent runs via a marker under
`/shared`.
