"""
Sandbox module for SecAgentFramework.

Provides unified interfaces for different sandbox implementations:
- BaseSandbox: Abstract base class defining the common interface
- NativeDockerSandbox: Direct Docker API implementation (with template fallback)
- SweRexSandbox: SWE-ReX based implementation (with template fallback)
- DockerfileBuilder: Jinja2-based dockerfile template builder
- TemplateFallbackMixin: Adds template fallback functionality to sandboxes

Template Fallback Feature:
Both NativeDockerSandbox and SweRexSandbox now support automatic fallback
to building images from Dockerfile templates when the specified image is
not available locally and cannot be pulled from registries.
"""

from .base_sandbox import BaseSandbox
from .docker_config import DockerConfig
from .dockerfile_builder import DockerBuildResult, DockerfileBuilder
from .native_docker_sandbox import NativeDockerSandbox
from .swe_rex_sandbox import SweRexSandbox
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
