"""
Sandbox module for OpenSage Framework.

Provides unified interfaces for different sandbox implementations:
- BaseSandbox: Abstract base class defining the common interface
- NativeDockerSandbox: Direct Docker API implementation
- DockerfileBuilder: Docker image builder using Dockerfiles with build args
- DockerBuildMixin: Adds dockerfile build functionality to sandboxes, if image is not available locally and cannot be pulled from registries, it will try to build the image from a Dockerfile.
"""

from __future__ import annotations

# Keep this package initializer lightweight to avoid import-time cycles.
#
# Import concrete backends/initializers from their modules directly.
from .base_sandbox import BaseSandbox, SandboxState

__all__ = [
    "BaseSandbox",
    "SandboxState",
]
