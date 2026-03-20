"""Sandbox factory for creating typed sandbox instances."""

from __future__ import annotations

from typing import Optional, Type

from opensage.sandbox.initializers import (
    CodeQLInitializer,
    CoverageInitializer,
    FuzzInitializer,
    GDBDebuggerInitializer,
    JoernInitializer,
    MainInitializer,
    Neo4jInitializer,
    SandboxInitializer,
)

from .agentdocker_lite_sandbox import AgentDockerLiteSandbox
from .base_sandbox import BaseSandbox
from .k8s_sandbox import K8sSandbox
from .local_sandbox import LocalSandbox
from .native_docker_sandbox import NativeDockerSandbox
from .opensandbox_sandbox import OpenSandboxSandbox
from .remote_docker_sandbox import RemoteDockerSandbox

# Registry of available backends
SANDBOX_BACKENDS = {
    "native": NativeDockerSandbox,
    "k8s": K8sSandbox,
    "remotedocker": RemoteDockerSandbox,
    "opensandbox": OpenSandboxSandbox,
    "agentdocker-lite": AgentDockerLiteSandbox,
    "local": LocalSandbox,
    # Future backends can be added here:
}

# Registry of available initializers
SANDBOX_INITIALIZERS = {
    "main": MainInitializer,
    "codeql": CodeQLInitializer,
    "joern": JoernInitializer,
    "fuzz": FuzzInitializer,
    "neo4j": Neo4jInitializer,
    "coverage": CoverageInitializer,
    "gdb_mcp": GDBDebuggerInitializer,
}


def create_sandbox_class(
    backend_class: Type[BaseSandbox], initializer_class: Type
) -> Type[BaseSandbox]:
    """
    Create a sandbox class by combining a backend with a initializer.

    Args:
        backend_class (Type[BaseSandbox]): The backend sandbox class (e.g., NativeDockerSandbox)
        initializer_class (Type): Initializer class to add functionality
    Returns:
        Type[BaseSandbox]: A new class that combines the backend and initializer
    """

    # Create a dynamic class that combines backend + initializer
    class CombinedSandbox(initializer_class, backend_class):
        """Dynamically created sandbox class with initializer functionality."""

        def __init__(
            self,
            container_config,
            session_id=None,
            backend_type=None,
            sandbox_type=None,
        ):
            # Initialize the backend
            backend_class.__init__(
                self, container_config, session_id, backend_type, sandbox_type
            )

    # Set a meaningful name for the combined class
    CombinedSandbox.__name__ = (
        f"{backend_class.__name__}With{initializer_class.__name__}"
    )
    CombinedSandbox.__qualname__ = CombinedSandbox.__name__

    return CombinedSandbox


def get_backend_class(backend_type: str, config=None) -> Type[BaseSandbox]:
    """
    Get the backend class for a given backend type.

    Args:
      backend_type (str): The type of backend needed (e.g., 'native', 'k8s')
      config: Optional config to inject into backend (for remotedocker)
    Returns:
      Type[BaseSandbox]: The backend class

    Raises:
      ValueError: If backend type is not supported
    """
    backend_class = SANDBOX_BACKENDS.get(backend_type)
    if backend_class is None:
        raise ValueError(f"Unsupported backend type: {backend_type}")

    # Inject config for remote docker backend
    if (
        backend_type in {"remotedocker", "opensandbox"}
        and config
        and hasattr(backend_class, "set_config")
    ):
        backend_class.set_config(config)

    return backend_class


def get_initializer_class(sandbox_type: str) -> Type:
    """
    Get the initializer class for a given sandbox type.

    Args:
        sandbox_type (str): The type of sandbox functionality needed
    Returns:
        Type: The initializer class, or the base SandboxInitializer if not found
    """
    return SANDBOX_INITIALIZERS.get(sandbox_type, SandboxInitializer)
