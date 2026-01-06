"""Sandbox factory for creating typed sandbox instances."""

from __future__ import annotations

from typing import Optional, Type

from aigise.sandbox.initializers import (
    CodeQLInitializer,
    CoverageInitializer,
    DebuggerInitializer,
    FuzzInitializer,
    JoernInitializer,
    MainInitializer,
    Neo4jInitializer,
    SandboxInitializer,
)

from .base_sandbox import BaseSandbox
from .k8s_sandbox import K8sSandbox
from .native_docker_sandbox import NativeDockerSandbox

# Registry of available backends
SANDBOX_BACKENDS = {
    "native": NativeDockerSandbox,
    "k8s": K8sSandbox,
    # Future backends can be added here:
    # "local": LocalSandbox,
}

# Registry of available initializers
SANDBOX_INITIALIZERS = {
    "main": MainInitializer,
    "codeql": CodeQLInitializer,
    "joern": JoernInitializer,
    "fuzz": FuzzInitializer,
    "neo4j": Neo4jInitializer,
    "coverage": CoverageInitializer,
    "debugger": DebuggerInitializer,
}


def create_sandbox_class(
    backend_class: Type[BaseSandbox], initializer_class: Type
) -> Type[BaseSandbox]:
    """
    Create a sandbox class by combining a backend with a initializer.

    Args:
        backend_class: The backend sandbox class (e.g., NativeDockerSandbox)
        initializer_class: Initializer class to add functionality

    Returns:
        A new class that combines the backend and initializer
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


def get_backend_class(backend_type: str) -> Type[BaseSandbox]:
    """
    Get the backend class for a given backend type.

    Args:
        backend_type: The type of backend needed (e.g., 'native', 'k8s')

    Returns:
        The backend class

    Raises:
        ValueError: If backend type is not supported
    """
    backend_class = SANDBOX_BACKENDS.get(backend_type)
    if backend_class is None:
        raise ValueError(f"Unsupported backend type: {backend_type}")
    return backend_class


def get_initializer_class(sandbox_type: str) -> Type:
    """
    Get the initializer class for a given sandbox type.

    Args:
        sandbox_type: The type of sandbox functionality needed

    Returns:
        The initializer class, or the base SandboxInitializer if not found
    """
    return SANDBOX_INITIALIZERS.get(sandbox_type, SandboxInitializer)
