from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from aigise.sandbox.docker_config import DockerConfig


class BaseSandbox(ABC):
    """Base class for all sandbox implementations."""

    def __init__(
        self,
        docker_config: DockerConfig,
    ):
        self.docker_config_obj = docker_config
        self.image_name = self.docker_config_obj.image

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
    def run_command_in_container(self, command: str | list[str]):
        """Run a command inside the container."""
        pass

    @abstractmethod
    def get_work_dir(self):
        """Get the current working directory in the container."""
        pass
