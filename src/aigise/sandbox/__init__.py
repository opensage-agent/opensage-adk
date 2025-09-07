"""
Sandbox module for SecAgentFramework.

Provides unified interfaces for different sandbox implementations:
- BaseSandbox: Abstract base class defining the common interface
- NativeDockerSandbox: Direct Docker API implementation
"""

from .base_sandbox import BaseSandbox
from .docker_config import DockerConfig
from .native_docker_sandbox import NativeDockerSandbox

__all__ = ["BaseSandbox", "NativeDockerSandbox", "DockerConfig"]
