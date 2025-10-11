"""
AIgiSE: AI Agent Framework

A comprehensive framework for security-focused AI agents with unified session management.

The framework provides session-isolated resource management through the AigiseSession
architecture, eliminating global singletons and providing clear separation of
concerns between different agent sessions.

Primary Interface:
    from aigise import get_aigise_session

    session = get_aigise_session("my_session_id")
    # All configuration, agent, and sandbox management through session
"""

# Export version
__version__ = "1.0.0"

# Primary session interface
# For backward compatibility and advanced usage
from .session import (
    AigiseSandboxManager,
    AigiseSession,
    AigiseSessionRegistry,
    DynamicAgentManager,
    cleanup_aigise_session,
    get_aigise_session,
)

__all__ = [
    # Primary interface
    "get_aigise_session",
    "cleanup_aigise_session",
    # Advanced/internal usage
    "AigiseSession",
    "AigiseSessionRegistry",
    "DynamicAgentManager",
    "AigiseSandboxManager",
]
