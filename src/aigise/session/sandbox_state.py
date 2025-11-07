from enum import Enum


class SandboxState(Enum):
    """Sandbox initialization states."""

    STARTING = "starting"
    CREATED = "created"  # Container created but not initialized
    READY = "ready"
    ERROR = "error"
