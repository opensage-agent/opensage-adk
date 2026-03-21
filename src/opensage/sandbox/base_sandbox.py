import asyncio
import logging
import subprocess
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from opensage.config import ContainerConfig

logger = logging.getLogger(__name__)


class SandboxState(Enum):
    """Sandbox initialization states."""

    UNINITIALIZED = "uninitialized"
    READY = "ready"
    ERROR = "error"


class BaseSandbox(ABC):
    """Base class for all sandbox implementations."""

    def __init__(
        self,
        container_config: ContainerConfig,
        opensage_session_id: str = None,
        backend_type: str = None,
        sandbox_type: str = None,
    ):
        self.container_config_obj = container_config
        self.opensage_session_id = opensage_session_id
        self.backend_type = backend_type
        self.sandbox_type = sandbox_type
        self.state = SandboxState.UNINITIALIZED

    async def async_initialize(self) -> None:
        """Initialize the sandbox."""
        pass

    async def ensure_ready(self) -> None:
        """Ensure the sandbox is ready."""
        pass

    async def wait_for_ready_or_error(self) -> bool:
        """Wait for a specific sandbox to be ready or error."""
        while self.state != SandboxState.READY and self.state != SandboxState.ERROR:
            await asyncio.sleep(1)
        if self.state == SandboxState.ERROR:
            logger.error(
                f"Waiting for sandbox '{self.sandbox_type}' in session {self.opensage_session_id} to be ready or error: result is error"
            )
            return False
        logger.info(
            f"Waiting for sandbox '{self.sandbox_type}' in session {self.opensage_session_id} to be ready or error: result is ready"
        )
        return True

    @abstractmethod
    def copy_file_from_container(self, src_path: str, dst_path: str):
        """Copy a file from the container to local filesystem."""
        pass

    @abstractmethod
    def copy_file_to_container(self, local_path: str, container_path: str):
        """Copy a file from local filesystem to the container."""
        pass

    @abstractmethod
    def extract_file_from_container(self, filepath: str) -> str:
        """Extract file content from the container."""
        pass

    @abstractmethod
    def extract_file_from_container_bytes(self, filepath: str) -> bytes:
        """Extract file content from the container."""
        pass

    @abstractmethod
    def run_command_in_container(
        self, command: str | list[str], timeout: int | None = None
    ) -> tuple[str, int]:
        """Run a command inside the container."""
        pass

    @abstractmethod
    def get_work_dir(self):
        """Get the current working directory in the container."""
        pass

    @abstractmethod
    def delete_container(self) -> None:
        """Delete the container."""
        pass

    @classmethod
    @abstractmethod
    def create_shared_volume(
        cls,
        volume_name_prefix: str,
        init_data_path: Path = None,
        tools_top_roots: set[str] | None = None,
    ) -> tuple[str, str, str]:
        """Create and initialize three shared volumes.

        Creates three volumes:
        1. Read-only volume with sandbox scripts (mapped to /sandbox_scripts)
        2. Read-write volume with user data (mapped to /shared)
        3. Read-write volume with bash tools (mapped to /bash_tools)

        Args:
            volume_name_prefix (str): Prefix for volume names (e.g., session_id)
            init_data_path (Path): Path to initial data to copy into the rw volume (optional)
            tools_top_roots (set[str] | None): Optional set of top-level bash_tools roots to stage.
                If None, stage all bash tools.
        Returns:
            tuple[str, str, str]: Tuple of (scripts_volume_id, data_volume_id, tools_volume_id)
        """
        pass

    @classmethod
    @abstractmethod
    async def create_single_sandbox(
        cls, session_id: str, sandbox_type: str, container_config
    ) -> Exception:
        """Create a single sandbox instance asynchronously."""
        pass

    @classmethod
    @abstractmethod
    async def launch_all_sandboxes(
        cls,
        session_id: str,
        sandbox_configs: dict,
        shared_volume_id: str = None,
        scripts_volume_id: str = None,
        tools_volume_id: str = None,
    ) -> dict:
        """Launch all sandbox instances for a session.

        Args:
            session_id (str): Session identifier
            sandbox_configs (dict): Dictionary of sandbox_type -> ContainerConfig
            shared_volume_id (str): Optional shared volume to mount to all sandboxes
            scripts_volume_id (str): Optional scripts volume to mount to all sandboxes
            tools_volume_id (str): Optional tools volume to mount to all sandboxes
        Returns:
            dict: Dictionary mapping sandbox_type to sandbox instance or connection info
        """
        pass

    @classmethod
    @abstractmethod
    def cache_sandboxes(
        cls,
        sandbox_instances: dict,
        shared_volume_id: str,
        cache_dir: str,
        task_name: str,
    ) -> dict:
        """Cache sandbox states and shared volume content.

        Args:
            sandbox_instances (dict): Dictionary mapping sandbox types to sandbox instances
            shared_volume_id (str): Shared volume identifier to backup
            cache_dir (str): Directory to store cache files
            task_name (str): Task name for cache naming
        Returns:
            dict: Dictionary with cache results including backup paths and cached images
        """
        pass

    @classmethod
    @abstractmethod
    def delete_shared_volumes(
        cls,
        scripts_volume_id: str = None,
        data_volume_id: str = None,
        tools_volume_id: str = None,
    ) -> None:
        """Delete shared volumes."""
        pass

    @classmethod
    @abstractmethod
    def checkpoint(cls) -> str:
        """Checkpoint the sandbox."""
        pass

    @classmethod
    @abstractmethod
    def restore(cls) -> str:
        """Restore the sandbox."""
        pass
