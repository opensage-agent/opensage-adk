from enum import Enum


class SandboxState(Enum):
    """Sandbox initialization states."""

    STARTING = "starting"
    READY = "ready"
    ERROR = "error"
