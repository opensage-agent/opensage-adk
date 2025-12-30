# Adding a New Sandbox Type

## Overview

Sandboxes provide isolated execution environments. You can add custom sandbox types with specific initialization logic.

## Steps

1. Create initializer in `src/aigise/sandbox/initializers/`
2. Implement `SandboxInitializer` interface
3. Add configuration in `config_dataclass.py`
4. Update default config template

## Example

```python
# src/aigise/sandbox/initializers/my_sandbox.py
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
