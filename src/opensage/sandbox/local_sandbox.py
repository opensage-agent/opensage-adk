import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from opensage.config.config_dataclass import ContainerConfig
from opensage.sandbox.base_sandbox import BaseSandbox

logger = logging.getLogger(__name__)


class LocalSandbox(BaseSandbox):
    backend_type = "local"

    def __init__(
        self,
        container_config: ContainerConfig,
        opensage_session_id: str = None,
        backend_type: str = None,
        sandbox_type: str = None,
    ):
        assert backend_type == "local", (
            f"LocalSandbox must have backend_type 'local', got {backend_type}"
        )
        super().__init__(
            container_config, opensage_session_id, backend_type, sandbox_type
        )

    def copy_file_from_container(self, src_path: str, dst_path: str):
        shutil.copyfile(src_path, dst_path)

    def copy_file_to_container(self, local_path: str, container_path: str):
        shutil.copyfile(local_path, container_path)

    def extract_file_from_container(self, filepath: str) -> str:
        with open(filepath, "r") as f:
            return f.read()

    def extract_file_from_container_bytes(self, filepath: str) -> bytes:
        with open(filepath, "rb") as f:
            return f.read()

    def run_command_in_container(
        self, command: str | list[str], timeout: int | None = None
    ) -> tuple[str, int]:
        if isinstance(command, list):
            command = shlex.join(command)

        command = [
            "/bin/bash",
            "-c",
            command,
        ]
        if timeout is not None:
            command = ["timeout", f"{timeout}s"] + command

        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            return result.stdout.decode(
                "utf-8", errors="backslashreplace"
            ), result.returncode
        except subprocess.TimeoutExpired:
            return "Command timed out", -1

    def get_work_dir(self):
        return os.getcwd()

    def delete_container(self) -> None:
        logger.info("LocalSandbox has no container to delete")

    @classmethod
    def create_shared_volume(
        cls,
        volume_name_prefix: str,
        init_data_path: Path = None,
        tools_top_roots: set[str] | None = None,
    ) -> tuple[str, str, str]:
        raise NotImplementedError("Shared volumes are not supported in LocalSandbox.")

    @classmethod
    async def create_single_sandbox(
        cls, session_id: str, sandbox_type: str, container_config: ContainerConfig
    ):
        raise NotImplementedError(
            "Creating single sandbox is not supported in LocalSandbox."
        )

    @classmethod
    async def launch_all_sandboxes(
        cls,
        session_id: str,
        sandbox_configs: dict,
        shared_volume_id: str = None,
        scripts_volume_id: str = None,
        tools_volume_id: str = None,
    ):
        assert len(sandbox_configs) == 1, "LocalSandbox supports only one sandbox."
        sandbox_instances = {}
        for sandbox_type, config in sandbox_configs.items():
            sandbox = cls(config, session_id, cls.backend_type, sandbox_type)
            sandbox_instances[sandbox_type] = sandbox
        return sandbox_instances

    @classmethod
    def cache_sandboxes(cls, sandbox_instances, shared_volume_id, cache_dir, task_name):
        return {
            "backend": "local",
            "task_name": task_name,
            "cache_dir": cache_dir,
            "shared_volume_backup": None,
            "cached_images": {},
            "errors": [],
        }

    @classmethod
    def delete_shared_volumes(
        cls, scripts_volume_id=None, data_volume_id=None, tools_volume_id=None
    ):
        pass

    @classmethod
    def checkpoint(cls) -> str:
        """Checkpoint the sandbox."""
        raise NotImplementedError("Checkpoint is not implemented for LocalSandbox")

    @classmethod
    def restore(cls) -> str:
        """Restore the sandbox."""
        raise NotImplementedError("Restore is not implemented for LocalSandbox")
