"""
Sandbox module for SecAgentFramework.

Provides unified interfaces for different sandbox implementations:
- BaseSandbox: Abstract base class defining the common interface
- NativeDockerSandbox: Direct Docker API implementation
- SweRexSandbox: SWE-ReX based implementation
"""

from .base_sandbox import BaseSandbox
from .native_docker_sandbox import NativeDockerSandbox
from .swe_rex_sandbox import SweRexSandbox

__all__ = ['BaseSandbox', 'NativeDockerSandbox', 'SweRexSandbox']
