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
        cls,
        session_id: str,
        docker_config: DockerConfig,
        sandbox_type: str = "main",
        backend: str = "swerex",
    ) -> BaseSandbox:
        """
        Get or create a sandbox for the given session_id.

        Args:
            session_id: The session identifier
            docker_config: Docker configuration for the sandbox
            sandbox_type: The type of sandbox to get or create (default: "main")
            backend: Backend to use ("swerex", "native").
                    "swerex" tries SWE-ReX first, falls back to native if it fails.
                    "native" uses native Docker directly without trying SWE-ReX.

        Returns:
            BaseSandbox: A sandbox instance for the session
        """
        if session_id in cls._instances and sandbox_type in cls._instances[session_id]:
            return cls._instances[session_id][sandbox_type]

        # Ensure nested dict exists
        if session_id not in cls._instances:
            cls._instances[session_id] = {}

        # Create new sandbox using the requested backend
        sandbox = cls._create_sandbox(docker_cfg=docker_config, backend=backend)
        cls._instances[session_id][sandbox_type] = sandbox

        logger.info(
            f"Created new sandbox for session {session_id} (type={sandbox_type}, backend={backend})"
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
        # If this session has no more sandboxes, remove the session entry
        if not cls._instances[session_id]:
            del cls._instances[session_id]
        logger.info(
            f"Cleaned up sandbox for session {session_id} (type={sandbox_type})"
        )

    @classmethod
    def cleanup_all(cls) -> None:
        """Clean up all sandbox instances."""
        # Make a copy of the instances dict to avoid modifying while iterating
        instances_copy = dict(cls._instances)
        for session_id, sandboxes in instances_copy.items():
            # Make a copy of the sandboxes dict to avoid modifying while iterating
            sandboxes_copy = dict(sandboxes)
            for sandbox_type in sandboxes_copy:
                try:
                    cls.cleanup_sandbox(session_id, sandbox_type)
                except Exception as e:
                    logger.warning(
                        f"Error cleaning up sandbox {sandbox_type} for session {session_id}: {e}"
                    )

        # Clear any remaining instances
        cls._instances.clear()

    @classmethod
    def _create_sandbox(
        cls, docker_cfg: DockerConfig, backend: str = "swerex"
    ) -> BaseSandbox:
        """
        Create a new sandbox instance using the specified backend.

        Args:
            docker_cfg: Docker configuration for the sandbox
            backend: Backend to use ("swerex", "native")

        Returns:
            BaseSandbox: A configured sandbox instance
        """
        IMAGE_NAME = os.getenv("IMAGE_NAME", "ubuntu:20.04")

        # Ensure docker_cfg has an image set
        if not docker_cfg.image:
            docker_cfg.image = IMAGE_NAME

        if backend == "native":
            # Direct use of Native Docker sandbox
            logger.info("Creating Native Docker sandbox (direct)")
            sandbox = NativeDockerSandbox(docker_config=docker_cfg)

            # Test Native Docker sandbox
            test_output, test_exit_code = sandbox.run_command_in_container(
                "echo 'native test'"
            )
            if test_exit_code == 0 and "native test" in test_output:
                logger.info("Created Native Docker sandbox")
                return sandbox
            else:
                raise Exception(f"Native Docker sandbox test failed: {test_output}")

        else:  # backend == "swerex"
            # Try SWE-ReX first, fallback to Native Docker
            logger.info("Creating SWE-ReX sandbox with native fallback")
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
                sandbox = NativeDockerSandbox(docker_config=docker_cfg)

                # Test Native Docker sandbox
                test_output, test_exit_code = sandbox.run_command_in_container(
                    "echo 'native test'"
                )
                if test_exit_code == 0 and "native test" in test_output:
                    logger.info("Created Native Docker sandbox")
                    return sandbox
                else:
                    raise Exception(f"Native Docker sandbox test failed: {test_output}")
