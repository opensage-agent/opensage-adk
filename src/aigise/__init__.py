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
import os
import sys

# Configure logging for AIgiSE module
# This will be executed once when the module is first imported


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colored output for different log levels."""

    # ANSI color codes
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record):
        # Add color to levelname
        levelname = record.levelname
        if levelname in self.COLORS:
            record.levelname = f"{self.COLORS[levelname]}{levelname}{self.RESET}"

        # Format the message
        result = super().format(record)

        # Restore original levelname for other handlers
        record.levelname = levelname
        return result


def _setup_logging():
    """Setup logging configuration for AIgiSE."""
    # Get log level from environment variable, default to DEBUG
    log_level_name = os.getenv("AIGISE_LOG_LEVEL", "DEBUG").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    # Check if we should use colors (disabled if NO_COLOR env var is set)
    use_colors = os.getenv("NO_COLOR") is None and sys.stderr.isatty()

    # Only configure if root logger hasn't been configured yet
    # This prevents overriding user's custom logging configuration
    if not logging.root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(log_level)

        # Use colored formatter if colors are enabled
        if use_colors:
            formatter = ColoredFormatter(
                fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        else:
            formatter = logging.Formatter(
                fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

        handler.setFormatter(formatter)

    # Set level for aigise logger specifically
    logging.getLogger("aigise").setLevel(log_level)
    logging.getLogger("aigise").addHandler(handler)


_setup_logging()

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
