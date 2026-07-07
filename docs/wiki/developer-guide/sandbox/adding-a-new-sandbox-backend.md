# Adding a new sandbox backend

## Overview

A sandbox backend defines **where/how** sandboxes run (local Docker, remote Docker, Kubernetes, local/no-container, etc.). In this repo, backends are implemented as `BaseSandbox` subclasses and selected by `sandbox.backend`.

This guide covers adding a **new backend** (not a new sandbox *type*).

## Steps

### 1) Implement a backend class

Create a new backend implementation under:

- `opensage-adk/src/opensage/sandbox/`

Backends are `BaseSandbox` subclasses (see `base_sandbox.py`) such as:

- `native_docker_sandbox.py`
- `remote_docker_sandbox.py`
- `k8s_sandbox.py`
- `local_sandbox.py`

### 2) Register the backend

Add an entry to the `_BACKENDS` dict in `src/opensage/sandbox/factory.py`, mapping the backend name to its `(module_name, class_name)` for lazy import:

```python
_BACKENDS = {
    # ...
    "mybackend": ("opensage.sandbox.mybackend_sandbox", "MyBackendSandbox"),
}
```

`get_backend_class(name)` reads `_BACKENDS`, imports the module on first use, and caches the resolved class in `SANDBOX_BACKENDS`. Assigning to `SANDBOX_BACKENDS` directly is unnecessary; the lazy path handles registration.

### 3) (Optional) Support config injection

If your backend needs configuration injected at runtime (similar to the remote docker backend), implement a `set_config(...)` classmethod and have the factory call it (see `get_backend_class(...)` in `factory.py`).

### 4) Use the backend in config

```toml
[sandbox]
backend = "mybackend"
```

## Tips

- Keep backend responsibilities focused on container/runtime management.
- Put “what to install/configure” in initializers (sandbox types), not in the backend.
