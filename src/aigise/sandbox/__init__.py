"""
Sandbox module for AIgiSE Framework.

Provides unified interfaces for different sandbox implementations:
- BaseSandbox: Abstract base class defining the common interface
- NativeDockerSandbox: Direct Docker API implementation
- DockerfileBuilder: Docker image builder using Dockerfiles with build args
- DockerBuildMixin: Adds dockerfile build functionality to sandboxes, if image is not available locally and cannot be pulled from registries, it will try to build the image from a Dockerfile.
"""

from .base_sandbox import BaseSandbox
from .k8s_sandbox import K8sSandbox
from .native_docker_sandbox import NativeDockerSandbox

__all__ = [
    "BaseSandbox",
    "NativeDockerSandbox",
    "K8sSandbox",
    "DockerfileBuilder",
    "DockerBuildResult",
    "ensure_docker_image",
]
