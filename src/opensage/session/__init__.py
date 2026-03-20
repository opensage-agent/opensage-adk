"""Session package public API.

This package intentionally keeps imports *minimal* to avoid import-time cycles
between `opensage.session`, `opensage.sandbox`, and `opensage.sandbox.initializers`.

If you need manager classes or other session types, import them from their
modules directly, e.g.:

- `from opensage.session.opensage_sandbox_manager import OpenSageSandboxManager`
- `from opensage.session.opensage_dynamic_agent_manager import AgentStatus`
"""

from __future__ import annotations

from .opensage_session import (
    OpenSageSession,
    OpenSageSessionRegistry,
    cleanup_opensage_session,
    get_opensage_session,
)

__all__ = [
    # Main session management
    "OpenSageSession",
    "OpenSageSessionRegistry",
    "get_opensage_session",
    "cleanup_opensage_session",
]
