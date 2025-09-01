from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseSandbox(ABC):
    """Base class for all sandbox implementations."""

    def __init__(
        self, image_name: str, compile_command: str, run_command: str, poc_dir: str
    ):
        """
        Initialize BaseSandbox with common properties.

        Args:
            image_name: Docker image name to use
            compile_command: Command to compile the target
            run_command: Command to run the target
            poc_dir: Directory for PoC files
        """
        self.image_name = image_name
        self.compile_command = compile_command
        self.run_command = run_command
        self.poc_dir = poc_dir

    @abstractmethod
    def copy_file_from_container(self, src_path: str, dst_path: str):
        """Copy a file from the container to local filesystem."""
        pass

    @abstractmethod
    def copy_file_to_container(self, local_path: str, container_path: str):
        """Copy a file from local filesystem to the container."""
        pass

    @abstractmethod
    def run_poc(self, poc_command: str):
        """Run a PoC command in the container."""
        pass

    @abstractmethod
    def compile_target(self):
        """Compile the target using the compile_command."""
        pass

    @abstractmethod
    def extract_file_from_container(self, filepath: str):
        """Extract file content from the container."""
        pass

    @abstractmethod
    def run_command_in_container(self, command: str):
        """Run a command inside the container."""
        pass

    @abstractmethod
    def get_work_dir(self):
        """Get the current working directory in the container."""
        pass
