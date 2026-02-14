"""Session package public API.

This package intentionally keeps imports *minimal* to avoid import-time cycles
between `aigise.session`, `aigise.sandbox`, and `aigise.sandbox.initializers`.

If you need manager classes or other session types, import them from their
modules directly, e.g.:

- `from aigise.session.aigise_sandbox_manager import AigiseSandboxManager`
- `from aigise.session.aigise_dynamic_agent_manager import AgentStatus`
"""

from __future__ import annotations

from .aigise_session import (
    AigiseSession,
    AigiseSessionRegistry,
    cleanup_aigise_session,
    get_aigise_session,
)

__all__ = [
    # Main session management
    "AigiseSession",
    "AigiseSessionRegistry",
    "get_aigise_session",
    "cleanup_aigise_session",
]
