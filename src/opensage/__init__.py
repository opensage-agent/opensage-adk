from __future__ import annotations

"""
OpenSage: AI Agent Framework

A comprehensive framework for security-focused AI agents with unified session management.

The framework provides session-isolated resource management through the OpenSageSession
architecture, eliminating global singletons and providing clear separation of
concerns between different agent sessions.

Primary Interface:
    from opensage import get_opensage_session

    session = get_opensage_session("my_session_id")
    # All configuration, agent, and sandbox management through session
"""

import logging

from opensage.utils.logs import log_to_tmp_folder, setup_opensage_logging

# Configure logging for OpenSage module
# This will be executed once when the module is first imported


def _setup_logging():
    """Initial automatic setup when module is imported.

    Only runs if no logging configuration exists yet (respects user's manual setup)
    """
    opensage_logger = logging.getLogger("opensage")
    if not opensage_logger.hasHandlers():
        setup_opensage_logging()


import litellm

litellm.disable_streaming_logging = True

# Apply OpenSage patches on import (wrappers are toggleable at runtime)
try:
    from .patches import apply_all as _apply_all_patches

    _apply_all_patches()
except Exception as _patch_err:
    logging.getLogger(__name__).warning(
        f"Failed to apply OpenSage patches: {_patch_err}"
    )

# Export version
__version__ = "1.0.0"

# Primary session interface
# For backward compatibility and advanced usage
# RL Framework integration (slime, verl, areal, etc.)
from .evaluation.rl_adapters import Client, RLSession, create
from .session.opensage_dynamic_agent_manager import DynamicAgentManager
from .session.opensage_sandbox_manager import OpenSageSandboxManager
from .session.opensage_session import (
    OpenSageSession,
    OpenSageSessionRegistry,
    cleanup_opensage_session,
    get_opensage_session,
)

__all__ = [
    # Primary interface
    "get_opensage_session",
    "cleanup_opensage_session",
    "setup_opensage_logging",
    "log_to_tmp_folder",
    # RL Framework integration
    "create",
    "Client",
    "RLSession",
    # Advanced/internal usage
    "OpenSageSession",
    "OpenSageSessionRegistry",
    "DynamicAgentManager",
    "OpenSageSandboxManager",
]
