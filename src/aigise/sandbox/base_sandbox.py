import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional

from aigise.config import ContainerConfig


class BaseSandbox(ABC):
    """Base class for all sandbox implementations."""

    def __init__(
        self,
        container_config: ContainerConfig,
        aigise_session_id: str = None,
        backend_type: str = None,
        sandbox_type: str = None,
    ):
        self.container_config_obj = container_config
        self.aigise_session_id = aigise_session_id
        self.backend_type = backend_type
        self.sandbox_type = sandbox_type

    async def async_initialize(self) -> None:
        """
        Base async initialization for all sandboxes.

        This method can be overridden by initializers to add specific functionality.
        The base implementation does nothing.
        """
        pass

    @abstractmethod
    def copy_file_from_container(self, src_path: str, dst_path: str):
        """Copy a file from the container to local filesystem."""
        pass

    @abstractmethod
    def copy_file_to_container(self, local_path: str, container_path: str):
        """Copy a file from local filesystem to the container."""
        pass

    @abstractmethod
    def extract_file_from_container(self, filepath: str):
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

    @classmethod
    @abstractmethod
    def create_shared_volume(cls, volume_name: str, init_data_path: Path) -> str:
        """Create and initialize a shared volume.

        Args:
            volume_name: Name of the volume to create
            init_data_path: Path to initial data to copy into the volume

        Returns:
            Volume identifier that can be used to reference the volume
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
        cls, session_id: str, sandbox_configs: dict, shared_volume_id: str = None
    ) -> dict:
        """Launch all sandbox instances for a session.

        Args:
            session_id: Session identifier
            sandbox_configs: Dictionary of sandbox_type -> ContainerConfig
            shared_volume_id: Optional shared volume to mount to all sandboxes

        Returns:
            Dictionary mapping sandbox_type to sandbox instance or connection info
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
            sandbox_instances: Dictionary mapping sandbox types to sandbox instances
            shared_volume_id: Shared volume identifier to backup
            cache_dir: Directory to store cache files
            task_name: Task name for cache naming

        Returns:
            Dictionary with cache results including backup paths and cached images
        """
        pass
