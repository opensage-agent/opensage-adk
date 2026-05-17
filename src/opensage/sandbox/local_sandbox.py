import logging
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from opensage.config.config_dataclass import ContainerConfig
from opensage.sandbox.base_sandbox import BaseSandbox, SandboxState

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
        self.state = SandboxState.READY

    def copy_file_from_container(self, src_path: str, dst_path: str):
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        shutil.copyfile(src_path, dst_path)

    def copy_file_to_container(self, local_path: str, container_path: str):
        os.makedirs(os.path.dirname(container_path), exist_ok=True)
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

        args = ["/bin/bash", "-c", command]

        try:
            result = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
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
        pass

    async def async_initialize(self, all_sandboxes=None) -> None:
        self.state = SandboxState.READY

    async def ensure_ready(self) -> None:
        self.state = SandboxState.READY

    @classmethod
    def create_shared_volume(
        cls,
        volume_name_prefix: str,
        init_data_path: Path = None,
        tools_top_roots: set[str] | None = None,
    ) -> tuple[str, str, str]:
        from opensage.sandbox.sandbox_paths import (
            get_bash_tools,
            get_sandbox_scripts,
            get_shared,
        )

        shared_dir = Path(get_shared())
        shared_dir.mkdir(parents=True, exist_ok=True)
        scripts_dir = Path(get_sandbox_scripts())
        scripts_dir.mkdir(parents=True, exist_ok=True)
        tools_dir = Path(get_bash_tools())
        tools_dir.mkdir(parents=True, exist_ok=True)

        return str(scripts_dir), str(shared_dir), str(tools_dir)

    @classmethod
    async def create_single_sandbox(
        cls, session_id: str, sandbox_type: str, container_config: ContainerConfig
    ):
        if sandbox_type != "main":
            raise ValueError(
                f"LocalSandbox only supports 'main' sandbox, got '{sandbox_type}'"
            )
        return cls(container_config, session_id, cls.backend_type, sandbox_type)

    @classmethod
    async def launch_all_sandboxes(
        cls,
        session_id: str,
        sandbox_configs: dict,
        shared_volume_id: str = None,
        scripts_volume_id: str = None,
        tools_volume_id: str = None,
    ):
        non_main = [k for k in sandbox_configs if k != "main"]
        if non_main:
            msg = (
                f"\n"
                f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
                f"!!!                                                     !!!\n"
                f"!!!  LocalSandbox: UNSUPPORTED SANDBOX TYPES DETECTED   !!!\n"
                f"!!!                                                     !!!\n"
                f"!!!  Backend 'local' only supports the 'main' sandbox.  !!!\n"
                f"!!!  The following will be SKIPPED: {non_main!r:<21s}!!!\n"
                f"!!!                                                     !!!\n"
                f"!!!  Tools requiring these sandboxes will NOT work.      !!!\n"
                f"!!!  Switch to 'native' or 'k8s' backend if needed.     !!!\n"
                f"!!!                                                     !!!\n"
                f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
            )
            logger.warning(msg)

        sandbox_instances = {}
        for sandbox_type, config in sandbox_configs.items():
            if sandbox_type != "main":
                continue
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
    async def initialize_all_sandboxes(
        cls,
        sandbox_instances: dict,
        *,
        continue_on_error: bool = False,
    ) -> dict:
        result_map = {}
        for sandbox_type, sandbox in sandbox_instances.items():
            sandbox.state = SandboxState.READY
            result_map[sandbox_type] = None
        return result_map

    @classmethod
    def checkpoint(cls) -> str:
        raise NotImplementedError("Checkpoint is not implemented for LocalSandbox")

    @classmethod
    def restore(cls) -> str:
        raise NotImplementedError("Restore is not implemented for LocalSandbox")
