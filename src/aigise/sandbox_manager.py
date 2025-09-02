"""
SandboxManager for managing sandbox instances per session.

This module provides a centralized way to manage sandbox instances,
creating them on-demand and cleaning them up when needed.
"""

import logging
import os
from typing import Any, Dict, Optional

from swerex.exceptions import SessionDoesNotExistError
from swerex.runtime.abstract import BashAction, CreateBashSessionRequest

from aigise.sandbox import BaseSandbox, NativeDockerSandbox, SweRexSandbox
from aigise.sandbox.docker_config import DockerConfig

logger = logging.getLogger(__name__)


class SandboxManager:
    """Manages sandbox instances per session_id and sandbox_type."""

    # Structure: { session_id: { sandbox_type: sandbox_instance } }
    _instances: Dict[str, Dict[str, BaseSandbox]] = {}

    @classmethod
    def get_sandbox(
        cls, session_id: str, docker_config: DockerConfig, sandbox_type: str = "main"
    ) -> BaseSandbox:
        """
        Get or create a sandbox for the given session_id.

        Args:
            session_id: The session identifier
            sandbox_type: The type of sandbox to get or create (default: "main")

        Returns:
            BaseSandbox: A sandbox instance for the session
        """
        if session_id in cls._instances and sandbox_type in cls._instances[session_id]:
            return cls._instances[session_id][sandbox_type]

        # Ensure nested dict exists
        if session_id not in cls._instances:
            cls._instances[session_id] = {}

        # Create new sandbox using the requested logic
        sandbox = cls._create_sandbox(
            docker_cfg=docker_config,
        )
        cls._instances[session_id][sandbox_type] = sandbox

        logger.info(
            f"Created new sandbox for session {session_id} (type={sandbox_type})"
        )
        return sandbox

    @classmethod
    def cleanup_sandbox(cls, session_id: str, sandbox_type: str = "main") -> None:
        """
        Clean up and remove sandbox for the given session_id.

        Args:
            session_id: The session identifier
        """
        if session_id not in cls._instances:
            return

        sandbox = cls._instances[session_id][sandbox_type]

        try:
            # Cleanup SWE-ReX resources if they exist
            if hasattr(sandbox, "deployment") and sandbox.deployment:
                try:
                    # SWE-ReX stop() is async, need to run it properly
                    import asyncio

                    asyncio.run(sandbox.deployment.stop())
                except Exception as e:
                    logger.warning(f"SWE-ReX deployment cleanup error: {e}")

            # Delete container for Native Docker
            if hasattr(sandbox, "delete_container"):
                sandbox.delete_container()

        except Exception as e:
            logger.warning(f"Error cleaning up sandbox for session {session_id}: {e}")

        del cls._instances[session_id][sandbox_type]
        logger.info(f"Cleaned up sandbox for session {session_id}")

    @classmethod
    def cleanup_all(cls) -> None:
        """Clean up all sandbox instances."""
        session_ids = list(cls._instances.keys())
        for session_id in session_ids:
            cls.cleanup_sandbox(session_id)

    @classmethod
    def _create_sandbox(cls, docker_cfg: DockerConfig) -> BaseSandbox:
        """
        Create a new sandbox instance using the fallback logic.
        Try SweRexSandbox first, fallback to NativeDockerSandbox if it fails.

        Returns:
            BaseSandbox: A configured sandbox instance
        """
        IMAGE_NAME = os.getenv("IMAGE_NAME", "ubuntu:20.04")

        sandbox = None
        try:
            # Try SWE-ReX sandbox first using DockerConfig
            sandbox = SweRexSandbox(docker_config=docker_cfg)
            logger.info("Created SweRexSandbox instance (runtime started)")
            return sandbox

        except Exception as e:
            logger.warning(f"SWE-ReX sandbox failed: {e}")
            logger.info("Falling back to Native Docker sandbox")

            # Cleanup SWE-ReX resources if they were created
            if sandbox and hasattr(sandbox, "deployment"):
                try:
                    # SWE-ReX stop() is async, need to run it properly
                    import asyncio

                    asyncio.run(sandbox.deployment.stop())
                except Exception as e:
                    logger.warning(f"SWE-ReX cleanup error during fallback: {e}")

            # Fallback to Native Docker sandbox
            sandbox = NativeDockerSandbox(
                image_name=IMAGE_NAME,
            )

            # Test Native Docker sandbox
            test_output, test_exit_code = sandbox.run_command_in_container(
                "echo 'native test'"
            )
            if test_exit_code == 0 and "native test" in test_output:
                logger.info("Created Native Docker sandbox")
                return sandbox
            else:
                raise Exception(f"Native Docker sandbox test failed: {test_output}")
