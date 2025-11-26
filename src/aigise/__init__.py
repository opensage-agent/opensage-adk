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

import logging

from aigise.utils.logs import log_to_tmp_folder, setup_aigise_logging

# Configure logging for AIgiSE module
# This will be executed once when the module is first imported


def _setup_logging():
    """Initial automatic setup when module is imported.

    Only runs if no logging configuration exists yet (respects user's manual setup)
    """
    aigise_logger = logging.getLogger("aigise")
    if not aigise_logger.handlers:
        setup_aigise_logging()


_setup_logging()
import litellm

litellm.disable_streaming_logging = True

# Apply AIgiSE patches on import (wrappers are toggleable at runtime)
try:
    from .patches import apply_all as _apply_all_patches

    _apply_all_patches()
except Exception as _patch_err:
    logging.getLogger(__name__).warning(f"Failed to apply AIgiSE patches: {_patch_err}")

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
    "setup_aigise_logging",
    "log_to_tmp_folder",
    # Advanced/internal usage
    "AigiseSession",
    "AigiseSessionRegistry",
    "DynamicAgentManager",
    "AigiseSandboxManager",
]
