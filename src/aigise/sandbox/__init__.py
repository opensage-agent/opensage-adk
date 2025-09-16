"""
Sandbox module for SecAgentFramework.

Provides unified interfaces for different sandbox implementations:
- BaseSandbox: Abstract base class defining the common interface
- NativeDockerSandbox: Direct Docker API implementation
- DockerfileBuilder: Jinja2-based dockerfile template builder
- TemplateFallbackMixin: Adds template fallback functionality to sandboxes, if image is not available locally and cannot be pulled from registries, it will try to build the image from the Dockerfile template.
"""

from .base_sandbox import BaseSandbox
from .docker_config import DockerConfig
from .dockerfile_builder import DockerBuildResult, DockerfileBuilder
from .native_docker_sandbox import NativeDockerSandbox
from .template_fallback import TemplateFallbackMixin, ensure_docker_image

__all__ = [
    "BaseSandbox",
    "NativeDockerSandbox",
    "DockerConfig",
    "DockerfileBuilder",
    "DockerBuildResult",
    "ensure_docker_image",
    "TemplateFallbackMixin",
]
