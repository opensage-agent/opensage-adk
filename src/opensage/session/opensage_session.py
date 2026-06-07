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
from typing import Dict, Optional

from ..config.config_dataclass import OpenSageConfig

logger = logging.getLogger(__name__)

# TODO: clearly define the session in opensage


class OpenSageSession:
    """
    Unified session manager for OpenSageAgent Framework.

    Each instance manages all resources for a specific session, including:
    - Configuration management (TOML loading, env overrides)
    - Agent lifecycle management (creation, persistence, cleanup)
    - Sandbox management (Docker containers, resource isolation)
    - Agent ensemble management (agent discovery)

    This replaces the previous singleton-based architecture with a clear
    session-bound resource management model.
    """

    def __init__(
        self,
        opensage_session_id: str,
        config_path: Optional[str] = None,
        agent_dir: Optional[str] = None,
    ):
        """Initialize OpenSageSession for a specific session.

        Args:
            opensage_session_id (str): Unique identifier for this session
            config_path (Optional[str]): Optional path to TOML configuration file
            agent_dir (Optional[str]): Directory the agent was loaded from. Used
                to resolve ``config.model.models_python_file`` when it is a
                relative path. Required only if the config references a relative
                python file path."""
        self.opensage_session_id = opensage_session_id
        self.agent_dir = agent_dir

        # Initialize session-specific configuration
        if config_path:
            self.config = OpenSageConfig.from_toml(config_path)
        else:
            self.config = OpenSageConfig.create_default()

        # Apply sandbox path configuration (mem_root, shared, etc.)
        from opensage.sandbox.sandbox_paths import configure_from_config

        configure_from_config(self.config)

        # Shared runtime LLM budget for this OpenSage session.
        from opensage.llm.budget import BudgetManager

        model_cfg = getattr(self.config, "model", None)
        self.budget = BudgetManager(
            configured_budget=getattr(model_cfg, "budget", 0.0) if model_cfg else 0.0,
            model_prices=getattr(model_cfg, "prices", None) if model_cfg else None,
        )

        # Initialize session-specific managers
        from .opensage_neo4j_client_manager import OpenSageNeo4jClientManager
        from .opensage_sandbox_manager import OpenSageSandboxManager

        self.sandboxes = OpenSageSandboxManager(self)
        # Neo4j client manager is retained for code-property-graph uses
        # (joern / codeql sandboxes). It is NOT used for conversation memory.
        self.neo4j = OpenSageNeo4jClientManager(self)

        # ----- ADK services owned by this environment -----
        from google.adk.artifacts.in_memory_artifact_service import (
            InMemoryArtifactService,
        )
        from google.adk.auth.credential_service.in_memory_credential_service import (
            InMemoryCredentialService,
        )
        from google.adk.memory.in_memory_memory_service import InMemoryMemoryService

        from ..features.opensage_in_memory_session_service import (
            OpenSageInMemorySessionService,
        )

        self.session_service = OpenSageInMemorySessionService()
        self.session_service.opensage_session = self  # back-reference
        self.artifact_service = InMemoryArtifactService()
        self.memory_service = InMemoryMemoryService()
        self.credential_service = InMemoryCredentialService()

        # ----- LlmRegistry (eager-loaded model pool for LLM-driven subagents) -----
        from ..llm import LlmRegistry

        self.llms = LlmRegistry.from_config(
            self.config,
            agent_dir=agent_dir,
            budget_manager=self.budget,
        )

        # ----- AgentManager (new orchestration layer) -----
        from ..orchestration.manager import AgentManager

        self.agent_manager = AgentManager(self)

        # Idempotency flag: cleanup() may be invoked from multiple layers
        # (explicit user code finally + atexit fallback). The 2nd+ call is a
        # no-op.
        self._cleaned_up: bool = False

        # root_session_id is set by the CLI / evaluation entrypoint after
        # spawning the root agent via agent_manager.spawn(...).
        self.root_session_id: Optional[str] = None

        logger.info(f"Created OpenSageSession for session: {opensage_session_id}")

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
        sandbox_stats = self.sandboxes.get_session_statistics()

        return {
            "opensage_session_id": self.opensage_session_id,
            "config_status": "loaded",
            "active_agents": len(self.agent_manager.list_instances()),
            "active_sandboxes": sandbox_stats["total_sandboxes"],
            "budget": self.budget.to_dict(),
        }

    def cleanup(self) -> None:
        """Synchronous, idempotent cleanup.

        Drops nothing across restarts: peer-message inboxes are not persisted
        across processes (they get reset in ``AgentManager.start``), and ADK
        traj.json is already written incrementally by the run patch. So
        cleanup only needs to release sandbox resources.

        Safe to call multiple times — repeated calls return immediately. This
        lets explicit finally blocks coexist with the atexit fallback without
        double-cleanup hazards.
        """
        if self._cleaned_up:
            return
        self._cleaned_up = True

        try:
            self.agent_manager.cancel_all_tasks()
        except Exception:
            logger.exception("agent task cancellation failed")

        if self.config.auto_cleanup:
            try:
                self.sandboxes.cleanup()
            except Exception:
                logger.exception("sandbox cleanup failed")


class OpenSageSessionRegistry:
    """
    Global registry for managing OpenSageSession instances.

    This is the only global singleton in the new architecture, responsible for:
    - Creating and tracking session managers
    - Preventing duplicate sessions
    - Coordinating session cleanup
    - Providing atexit-based cleanup as a safety net
    """

    _sessions: Dict[str, OpenSageSession] = {}

    @staticmethod
    def _cleanup_at_exit():
        """Cleanup all sessions at exit, ignoring closed stream errors."""
        try:
            import logging as _logging  # pylint: disable=g-import-not-at-top

            _logging.raiseExceptions = False
            OpenSageSessionRegistry.cleanup_all_sessions()
        except (ValueError, OSError):
            pass

    atexit.register(_cleanup_at_exit)

    @classmethod
    def get_opensage_session(
        cls,
        opensage_session_id: str,
        config_path: Optional[str] = None,
        create_if_missing: bool = True,
        agent_dir: Optional[str] = None,
    ) -> OpenSageSession:
        """
        Get or create a session manager for the given session ID.

        Args:
            opensage_session_id (str): Unique session identifier
            config_path: Optional TOML path; only used on first creation.
            create_if_missing: If False and the session is not already in the
                registry, return None.
            agent_dir: Directory the agent was loaded from; passed through to
                ``OpenSageSession`` for resolving relative ``models_python_file``.
                Only used on first creation.
        Returns:
            OpenSageSession: OpenSageSession instance for the session
        """
        if opensage_session_id not in cls._sessions:
            if not create_if_missing:
                return None
            cls._sessions[opensage_session_id] = OpenSageSession(
                opensage_session_id, config_path, agent_dir=agent_dir
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
    agent_dir: Optional[str] = None,
) -> OpenSageSession:
    """
    Get or create an OpenSageSession for the given session ID.
    """
    return OpenSageSessionRegistry.get_opensage_session(
        opensage_session_id,
        config_path,
        create_if_missing,
        agent_dir=agent_dir,
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
