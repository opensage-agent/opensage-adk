"""
OpenSageSession: Unified session management for OpenSageAgent Framework

This module provides the primary session management architecture that consolidates
all session-specific managers (config, agents, sandboxes) under a unified interface.

Each OpenSageSession instance represents a single session and manages all
resources for that session without relying on global singletons.
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional

from ..config.config_dataclass import OpenSageConfig
from ..utils.project_info import PROJECT_PATH

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .message_board import MessageBoardManager

# TODO: clearly define the session in opensage


class OpenSageSession:
    """
    Unified session manager for OpenSageAgent Framework.

    Each instance manages all resources for a specific session, including:
    - Configuration management (TOML loading, env overrides)
    - Agent lifecycle management (creation, persistence, cleanup)
    - Sandbox management (Docker containers, resource isolation)
    - Agent ensemble management (thread-safe tools, agent discovery)

    This replaces the previous singleton-based architecture with a clear
    session-bound resource management model.
    """

    def __init__(self, opensage_session_id: str, config_path: Optional[str] = None):
        """Initialize OpenSageSession for a specific session.

        Args:
            opensage_session_id (str): Unique identifier for this session
            config_path (Optional[str]): Optional path to TOML configuration file"""
        self.opensage_session_id = opensage_session_id

        # Initialize session-specific configuration
        if config_path:
            self.config = OpenSageConfig.from_toml(config_path)
        else:
            self.config = OpenSageConfig.create_default()

        # Initialize memory settings from config (lazy import to avoid circular dependency)
        if self.config.memory:
            from ..memory.config import configure_memory_from_config

            configure_memory_from_config(self.config.memory)

        # Initialize all session-specific managers
        # Pass self (session) instead of individual fields to allow dynamic property access
        from .opensage_dynamic_agent_manager import DynamicAgentManager
        from .opensage_ensemble_manager import OpenSageEnsembleManager
        from .opensage_neo4j_client_manager import OpenSageNeo4jClientManager
        from .opensage_sandbox_manager import OpenSageSandboxManager

        self.agents = DynamicAgentManager(self)
        self.sandboxes = OpenSageSandboxManager(self)
        self.neo4j = OpenSageNeo4jClientManager(self)
        self.ensemble = OpenSageEnsembleManager(self)

        self._message_boards_by_id: Dict[str, "MessageBoardManager"] = {}

        logger.info(f"Created OpenSageSession for session: {opensage_session_id}")

    def get_message_board(self, *, board_id: str | None = None):
        """Get a message board for the current session.

                Message boards are created on-demand and are intended for ensemble runs.

        Raises:
          ValueError: Raised when this operation fails."""
        if not board_id:
            raise ValueError("board_id is required for message boards")

        existing = self._message_boards_by_id.get(board_id)
        if existing is not None:
            return existing

        from .message_board import (
            MessageBoardManager,  # pylint: disable=g-import-not-at-top
        )

        board = MessageBoardManager(
            base_dir=Path("/tmp"),
            session_id=self.opensage_session_id,
            board_id=board_id,
        )
        self._message_boards_by_id[board_id] = board
        return board

    def cleanup_message_board(self, *, board_id: str) -> None:
        """Cleanup a temporary message board by id (best-effort)."""
        if not board_id:
            return
        board = self._message_boards_by_id.pop(board_id, None)
        if board is None:
            return
        board.cleanup()

    def load_config_from_toml(self, toml_path: str) -> None:
        """
        Load configuration from TOML file for this session.

        Args:
            toml_path (str): Path to TOML configuration file"""
        self.config = OpenSageConfig.from_toml(toml_path)

    def save_config_to_toml(self, toml_path: str) -> None:
        """
        Save current configuration to TOML file.

        Args:
            toml_path (str): Path to save TOML file"""
        self.config.save_to_toml(toml_path)

    def update_config_from_env(self) -> None:
        """Update configuration from environment variables."""
        self.config = OpenSageConfig.create_default()

    def get_session_info(self) -> Dict:
        """
        Get comprehensive information about this session.

        Returns:
            Dict: Dictionary containing session information
        """
        agent_stats = self.agents.get_session_statistics()
        sandbox_stats = self.sandboxes.get_session_statistics()
        thread_safe_tools = self.ensemble.get_thread_safe_tools()

        return {
            "opensage_session_id": self.opensage_session_id,
            "config_status": "loaded",
            "active_agents": agent_stats["total_agents"],
            "active_sandboxes": sandbox_stats["total_sandboxes"],
            "thread_safe_tools_count": len(thread_safe_tools),
        }

    def cleanup(self) -> None:
        """
        Cleanup all resources for this session.
        """
        if (
            self.config.auto_cleanup
        ):  # TODO: this auto_cleanup flag seems not very useful?
            self.sandboxes.cleanup()
            self.agents.cleanup()
            self.ensemble.cleanup()


class OpenSageSessionRegistry:
    """
    Global registry for managing OpenSageSession instances.

    This is the only global singleton in the new architecture, responsible for:
    - Creating and tracking session managers
    - Preventing duplicate sessions
    - Coordinating session cleanup
    - Managing global signal handlers for graceful shutdown
    """

    _sessions: Dict[str, OpenSageSession] = {}

    # Setup signal handlers directly
    def _signal_handler(signum, frame):
        # Use print instead of logger to avoid reentrant logging errors
        print(f"Received signal {signum}, cleaning up all sessions...", flush=True)
        try:
            OpenSageSessionRegistry.cleanup_all_sessions()
        except Exception:
            pass  # Ignore errors during signal handler cleanup
        # Use a graceful process exit so outer layers (e.g., CLI finally blocks)
        # can persist session snapshots before termination.
        sys.exit(0)

    def _cleanup_at_exit():
        """Cleanup all sessions at exit, ignoring closed stream errors."""
        try:
            # Avoid noisy "Logging error: I/O operation on closed file" messages
            # when pytest (or other runners) close stderr/stdout before atexit.
            import logging as _logging  # pylint: disable=g-import-not-at-top

            _logging.raiseExceptions = False
            OpenSageSessionRegistry.cleanup_all_sessions()
        except (ValueError, OSError):
            # Ignore errors from logging to closed streams during shutdown
            pass

    try:
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
        logger.info("Signal handlers registered for graceful session cleanup")
    except (ValueError, OSError):
        pass
    atexit.register(_cleanup_at_exit)

    @classmethod
    def get_opensage_session(
        cls,
        opensage_session_id: str,
        config_path: Optional[str] = None,
        create_if_missing: bool = True,
    ) -> OpenSageSession:
        """
        Get or create a session manager for the given session ID.

        Args:
            opensage_session_id (str): Unique session identifier
        Returns:
            OpenSageSession: OpenSageSession instance for the session
        """
        if opensage_session_id not in cls._sessions:
            if not create_if_missing:
                return None
            cls._sessions[opensage_session_id] = OpenSageSession(
                opensage_session_id, config_path
            )
            logger.info(f"Created new session in registry: {opensage_session_id}")

        return cls._sessions[opensage_session_id]

    @classmethod
    def list_sessions(cls) -> list[str]:
        """
        Get list of all active session IDs.

        Returns:
            list[str]: List of active session IDs
        """
        return list(cls._sessions.keys())

    @classmethod
    def remove_session(cls, opensage_session_id: str) -> bool:
        """
        Remove and cleanup a session.

        Args:
            opensage_session_id (str): Session ID to remove
        Returns:
            bool: True if removed, False if not found
        """
        if opensage_session_id not in cls._sessions:
            return False

        # Cleanup the session manager
        cls._sessions[opensage_session_id].cleanup()

        # Remove from registry
        del cls._sessions[opensage_session_id]

        logger.info(f"Removed session from registry: {opensage_session_id}")
        return True

    @classmethod
    def cleanup_all_sessions(cls) -> None:
        """
        Cleanup all active sessions.

        This should be called during application shutdown to ensure
        all resources are properly cleaned up.
        """
        logger.info("Cleaning up all sessions")

        # Make a copy to avoid modifying dict during iteration
        opensage_session_ids = list(cls._sessions.keys())
        for opensage_session_id in opensage_session_ids:
            cls.remove_session(opensage_session_id)

        logger.info("All sessions cleaned up")


def get_opensage_session(
    opensage_session_id: str,
    config_path: Optional[str] = None,
    create_if_missing: bool = True,
) -> OpenSageSession:
    """
    Get or create an OpenSageSession for the given session ID.
    """
    return OpenSageSessionRegistry.get_opensage_session(
        opensage_session_id, config_path, create_if_missing
    )


def cleanup_opensage_session(opensage_session_id: str) -> bool:
    """
    Cleanup and remove an OpenSageSession.

    Args:
        opensage_session_id (str): Session ID to cleanup
    Returns:
        bool: True if cleaned up, False if not found

    Example:
        cleanup_opensage_session("user_123_task_456")
    """
    return OpenSageSessionRegistry.remove_session(opensage_session_id)
