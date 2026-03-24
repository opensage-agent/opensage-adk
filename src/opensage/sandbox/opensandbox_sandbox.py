from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import shlex
import tarfile
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import docker

from opensage.config import ContainerConfig, OpenSageConfig, OpenSandboxConfig
from opensage.sandbox.base_sandbox import BaseSandbox, SandboxState
from opensage.sandbox.k8s_sandbox import K8sSandbox
from opensage.sandbox.remote_docker_sandbox import RemoteDockerSandbox
from opensage.sandbox.shared_storage import SharedStorage, _temporary_env
from opensage.utils.parser import get_function_info

logger = logging.getLogger(__name__)


class OpenSandboxSandbox(BaseSandbox):
    """OpenSage sandbox backend powered by OpenSandbox."""

    backend_type = "opensandbox"
    _injected_config: Optional[OpenSageConfig] = None
    _CACHE_DIR_ENV = "OPENSAGE_OPENSANDBOX_CACHE_DIR"
    _HELPER_IMAGE = "alpine:latest"
    _OPENSANDBOX_ID_LABEL = "opensandbox.io/id"

    def __init__(
        self,
        container_config: ContainerConfig,
        session_id: str = None,
        backend_type: str = None,
        sandbox_type: str = None,
    ):
        if container_config is None or not isinstance(
            container_config, ContainerConfig
        ):
            raise TypeError("container_config must be a ContainerConfig instance")
        if not container_config.image:
            raise ValueError("ContainerConfig must have image for opensandbox backend")

        super().__init__(
            container_config,
            session_id,
            backend_type or self.backend_type,
            sandbox_type,
        )
        self.opensandbox_id: Optional[str] = None
        self.execd_endpoint = None
        self.command_service = None
        self.filesystem_service = None
        self.health_service = None
        self.metrics_service = None

        connection_config = self._build_connection_config_sync()
        self._connection_config = connection_config

        from opensandbox.sync.adapters.factory import AdapterFactorySync

        self._adapter_factory = AdapterFactorySync(connection_config)
        self.sandbox_service = self._adapter_factory.create_sandbox_service()

    @classmethod
    def set_config(cls, config: OpenSageConfig) -> None:
        cls._injected_config = config

    @classmethod
    def _get_global_config(cls) -> OpenSageConfig:
        if cls._injected_config is None:
            raise ValueError(
                "OpenSandboxSandbox config has not been injected. "
                "Use get_backend_class('opensandbox', config) before invoking backend class methods."
            )
        return cls._injected_config

    @classmethod
    def _get_global_opensandbox_config(cls) -> OpenSandboxConfig:
        config = cls._get_global_config()
        if not config.sandbox or not config.sandbox.opensandbox:
            raise ValueError(
                "sandbox.opensandbox configuration is required for opensandbox backend"
            )
        return config.sandbox.opensandbox

    def _get_opensandbox_config(self) -> OpenSandboxConfig:
        config = self._get_global_config()
        if not config.sandbox or not config.sandbox.opensandbox:
            raise ValueError(
                "sandbox.opensandbox configuration is required for opensandbox backend"
            )
        return config.sandbox.opensandbox

    def _build_connection_config_sync(self):
        opensandbox_config = self._get_opensandbox_config()
        from opensandbox.config.connection_sync import ConnectionConfigSync

        connection_config = ConnectionConfigSync(
            api_key=opensandbox_config.api_key,
            domain=opensandbox_config.domain,
            protocol=opensandbox_config.protocol,
            request_timeout=timedelta(seconds=opensandbox_config.request_timeout_sec),
            use_server_proxy=opensandbox_config.use_server_proxy,
        )
        return connection_config.with_transport_if_missing()

    @staticmethod
    def _normalize_cache_name(name: str) -> str:
        normalized = name.lower()
        normalized = re.sub(r"[^a-z0-9._-]", "_", normalized)
        normalized = normalized.strip(".-")
        if normalized.startswith("_"):
            normalized = "img" + normalized
        if len(normalized) > 200:
            normalized = normalized[:200].rstrip("_-.")
        return normalized

    def _parse_legacy_mounts_to_opensandbox_volumes(self):
        from opensandbox.models.sandboxes import PVC, Host, Volume

        volumes = []
        for index, spec in enumerate(self.container_config_obj.volumes or []):
            if not isinstance(spec, str):
                raise TypeError(
                    f"Unsupported volume spec type for opensandbox backend: {type(spec)}"
                )
            parts = spec.split(":")
            if len(parts) < 2:
                raise ValueError(f"Invalid volume spec for opensandbox backend: {spec}")
            source = parts[0]
            mount_path = parts[1]
            mode = parts[2] if len(parts) > 2 else "rw"
            volume_name = self._sanitize_volume_name(
                f"{self.sandbox_type}-{index}-{mount_path.strip('/').replace('/', '-') or 'root'}"
            )
            volume_backend: dict[str, Any]
            if source.startswith("/"):
                volume_backend = {"host": Host(path=source)}
            else:
                volume_backend = {"pvc": PVC(claim_name=source)}
            volumes.append(
                Volume(
                    name=volume_name,
                    **volume_backend,
                    mount_path=mount_path,
                    read_only=mode == "ro",
                )
            )
        return volumes

    @staticmethod
    def _sanitize_volume_name(value: str) -> str:
        slug = re.sub(r"[^a-z0-9-]", "-", value.lower())
        slug = re.sub(r"-+", "-", slug).strip("-")
        slug = slug[:63].rstrip("-")
        return slug or "volume"

    def _build_entrypoint(self) -> list[str]:
        command = getattr(self.container_config_obj, "command", None)
        if command is None or command == "":
            return ["/bin/sh", "-c", "while true; do sleep 1000; done"]
        if isinstance(command, str):
            return ["/bin/sh", "-c", command]
        if isinstance(command, (list, tuple)):
            return list(command)
        raise TypeError(
            f"Unsupported command type for opensandbox backend: {type(command)}"
        )

    def _build_resource_limits(self) -> dict[str, str]:
        resource_limits: dict[str, str] = {}
        if self.container_config_obj.mem_limit:
            resource_limits["memory"] = str(self.container_config_obj.mem_limit)
        if self.container_config_obj.cpus:
            resource_limits["cpu"] = str(self.container_config_obj.cpus)
        return resource_limits

    def _get_timeout_seconds(self) -> int:
        opensandbox_config = self._get_opensandbox_config()
        extra = self.container_config_obj.extra or {}
        return int(
            extra.get("opensandbox_timeout_sec", opensandbox_config.default_timeout_sec)
        )

    def _get_command_working_directory(self) -> Optional[str]:
        opensandbox_config = self._get_opensandbox_config()
        return (
            self.container_config_obj.working_dir
            or opensandbox_config.request_working_directory
        )

    def _create_remote_sandbox(self) -> None:
        from opensandbox.models.sandboxes import SandboxImageSpec

        opensandbox_config = self._get_opensandbox_config()
        response = self.sandbox_service.create_sandbox(
            spec=SandboxImageSpec(self.container_config_obj.image),
            entrypoint=self._build_entrypoint(),
            env={
                key: str(value)
                for key, value in (self.container_config_obj.environment or {}).items()
                if value is not None
            },
            metadata={
                "opensage.session_id": self.opensage_session_id or "",
                "opensage.sandbox_type": self.sandbox_type or "",
            },
            timeout=timedelta(seconds=self._get_timeout_seconds()),
            resource=self._build_resource_limits(),
            network_policy=None,
            extensions={},
            volumes=self._parse_legacy_mounts_to_opensandbox_volumes(),
        )
        self.opensandbox_id = response.id
        self._bind_execd_clients()
        self._wait_for_execd_ready(
            timeout_seconds=opensandbox_config.request_timeout_sec
        )

    def _bind_execd_clients(self) -> None:
        if not self.opensandbox_id:
            raise ValueError("Cannot bind execd clients before sandbox creation")
        opensandbox_config = self._get_opensandbox_config()
        self.execd_endpoint = self.sandbox_service.get_sandbox_endpoint(
            self.opensandbox_id,
            opensandbox_config.execd_port,
            opensandbox_config.use_server_proxy,
        )
        self.command_service = self._adapter_factory.create_command_service(
            self.execd_endpoint
        )
        self.filesystem_service = self._adapter_factory.create_filesystem_service(
            self.execd_endpoint
        )
        self.health_service = self._adapter_factory.create_health_service(
            self.execd_endpoint
        )
        self.metrics_service = self._adapter_factory.create_metrics_service(
            self.execd_endpoint
        )

    def _wait_for_execd_ready(self, *, timeout_seconds: int) -> None:
        if self.health_service is None:
            raise ValueError("Health service is not bound")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.health_service.ping(self.opensandbox_id or ""):
                return
            time.sleep(1)
        raise RuntimeError(
            f"OpenSandbox execd endpoint did not become ready for "
            f"{self.sandbox_type} in session {self.opensage_session_id}"
        )

    def copy_file_from_container(self, src_path: str, dst_path: str):
        data = self.extract_file_from_container_bytes(src_path)
        Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
        with open(dst_path, "wb") as out_file:
            out_file.write(data)

    def copy_file_to_container(self, local_path: str, container_path: str):
        from opensandbox.models.filesystem import WriteEntry

        parent_dir = os.path.dirname(container_path)
        if parent_dir:
            self.filesystem_service.create_directories([WriteEntry(path=parent_dir)])
        with open(local_path, "rb") as in_file:
            self.filesystem_service.write_file(container_path, in_file)

    def copy_directory_from_container(self, src_path: str, dst_path: str):
        archive_path = f"/tmp/opensage_copy_{self.sandbox_type}.tar.gz"
        self.run_command_in_container(
            [
                "/bin/sh",
                "-c",
                f"tar -C {shlex.quote(src_path)} -czf {shlex.quote(archive_path)} .",
            ]
        )
        data = self.extract_file_from_container_bytes(archive_path)
        if os.path.exists(dst_path):
            import shutil

            shutil.rmtree(dst_path)
        os.makedirs(dst_path, exist_ok=True)
        with tempfile.NamedTemporaryFile(delete=False) as temp_tar:
            temp_tar.write(data)
            temp_tar_path = temp_tar.name
        try:
            with tarfile.open(temp_tar_path, "r:gz") as tar:
                tar.extractall(dst_path)
        finally:
            os.remove(temp_tar_path)
            self.run_command_in_container(["rm", "-f", archive_path])

    def copy_directory_to_container(self, src_path: str, dst_path: str):
        archive_path = f"/tmp/opensage_upload_{self.sandbox_type}.tar.gz"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as temp_tar:
            temp_tar_path = temp_tar.name
        try:
            with tarfile.open(temp_tar_path, "w:gz") as tar:
                tar.add(src_path, arcname=".")
            self.copy_file_to_container(temp_tar_path, archive_path)
            self.run_command_in_container(
                [
                    "/bin/sh",
                    "-c",
                    f"mkdir -p {shlex.quote(dst_path)} && "
                    f"tar -C {shlex.quote(dst_path)} -xzf {shlex.quote(archive_path)} && "
                    f"rm -f {shlex.quote(archive_path)}",
                ]
            )
        finally:
            if os.path.exists(temp_tar_path):
                os.remove(temp_tar_path)

    def extract_file_from_container(self, filepath: str) -> str:
        data = self.extract_file_from_container_bytes(filepath)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("latin-1", errors="replace")

    def extract_file_from_container_bytes(self, filepath: str) -> bytes:
        return self.filesystem_service.read_bytes(filepath)

    def create_tar_bytes(self, file_content: str, arcname: str) -> bytes:
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            file_bytes = file_content.encode()
            tarinfo = tarfile.TarInfo(name=arcname)
            tarinfo.size = len(file_bytes)
            tar.addfile(tarinfo, io.BytesIO(file_bytes))
        tar_stream.seek(0)
        return tar_stream.read()

    def patch_search_replace(self, file: str, search: str, replace: str):
        file_content = self.extract_file_from_container(file)
        modified_content = file_content.replace(search, replace)
        with tempfile.NamedTemporaryFile("w", delete=False) as tmp_file:
            tmp_file.write(modified_content)
            tmp_path = tmp_file.name
        try:
            self.copy_file_to_container(tmp_path, file)
        finally:
            os.unlink(tmp_path)

    def patch_file_func(self, files_func_to_content: dict[str, str], lang: str = "c"):
        for key, new_function_content in files_func_to_content.items():
            parts = key.split("__xx__")
            if len(parts) != 2:
                logger.warning(
                    "Key %s is not in the correct format. Expected format: "
                    "'filepath__xx__functionname'",
                    key,
                )
                continue
            filepath, function_name = parts
            file_content = self.extract_file_from_container(filepath)
            functions = get_function_info(file_content, lang)
            if function_name not in functions:
                logger.warning(
                    "Initial try, Function %s not found in file %s",
                    function_name,
                    filepath,
                )
                func_name = function_name.split("::")[-1]
                if func_name in functions:
                    function_name = func_name
                else:
                    potential_funcs = [
                        func
                        for func in functions
                        if func_name in func or func in func_name
                    ]
                    if potential_funcs:
                        potential_funcs.sort(key=lambda f: abs(len(f) - len(func_name)))
                        function_name = potential_funcs[0]
                    else:
                        continue

            start_line, end_line = functions[function_name][0]
            file_lines = file_content.splitlines()
            modified_lines = (
                file_lines[: start_line - 1]
                + new_function_content.splitlines()
                + file_lines[end_line:]
            )
            modified_file_content = "\n".join(modified_lines)
            with tempfile.NamedTemporaryFile("w", delete=False) as tmp_file:
                tmp_file.write(modified_file_content)
                tmp_path = tmp_file.name
            try:
                self.copy_file_to_container(tmp_path, filepath)
            finally:
                os.unlink(tmp_path)

    def get_function_content(
        self, key: str, lang: str = "c", line_in_func: int = -1
    ) -> tuple[str, int, int]:
        parts = key.split("__xx__")
        if len(parts) != 2:
            logger.warning(
                "Key %s is not in the correct format. Expected format: "
                "'filepath__xx__functionname'",
                key,
            )
            return "", -1, -1
        filepath, function_name = parts
        file_content = self.extract_file_from_container(filepath)
        functions = get_function_info(file_content, lang)
        if function_name not in functions:
            func_name = function_name.split("::")[-1]
            if func_name in functions:
                function_name = func_name
            else:
                return "", -1, -1
        if line_in_func != -1:
            for scope in functions[function_name]:
                start_line, end_line = scope
                if start_line <= line_in_func <= end_line:
                    break
        else:
            start_line, end_line = functions[function_name][-1]
        file_lines = file_content.splitlines()
        function_lines = file_lines[start_line - 1 : end_line]
        return "\n".join(function_lines), start_line, end_line

    def get_file_content(self, filepath: str) -> str:
        return self.extract_file_from_container(filepath)

    def run_command_in_container(
        self, command: str | list[str], timeout: int | None = None
    ) -> tuple[str, int]:
        from opensandbox.models.execd import RunCommandOpts
        from opensandbox.models.execd_sync import ExecutionHandlersSync

        if self.command_service is None:
            raise RuntimeError("OpenSandbox command service is not initialized")

        output_chunks: list[str] = []
        if isinstance(command, list):
            command = shlex.join(command)

        handlers = ExecutionHandlersSync(
            on_stdout=lambda msg: output_chunks.append(msg.text),
            on_stderr=lambda msg: output_chunks.append(msg.text),
        )
        execution = self.command_service.run(
            command,
            opts=RunCommandOpts(
                background=False,
                working_directory=self._get_command_working_directory(),
                timeout=timedelta(seconds=timeout) if timeout else None,
            ),
            handlers=handlers,
        )
        exit_code = 0
        if execution.id:
            status = self.command_service.get_command_status(execution.id)
            if status.exit_code is not None:
                exit_code = status.exit_code
        if execution.error is not None and exit_code == 0:
            exit_code = 1
        return "".join(output_chunks), exit_code

    def get_work_dir(self):
        output, _ = self.run_command_in_container("pwd")
        return output.strip()

    def delete_container(self) -> None:
        if not self.opensandbox_id:
            return
        try:
            self.sandbox_service.kill_sandbox(self.opensandbox_id)
        except Exception as exc:
            logger.warning(
                "Failed to delete OpenSandbox sandbox %s: %s",
                self.opensandbox_id,
                exc,
            )
        finally:
            self.opensandbox_id = None

    @classmethod
    def create_shared_volume(
        cls,
        volume_name_prefix: str,
        init_data_path: Path = None,
        tools_top_roots: set[str] | None = None,
    ) -> tuple[str, str, str]:
        return SharedStorage.create_for_opensandbox(
            volume_name_prefix,
            init_data_path,
            tools_top_roots,
            cls._get_global_config(),
        )

    @classmethod
    def delete_shared_volumes(
        cls,
        scripts_volume_id: str = None,
        data_volume_id: str = None,
        tools_volume_id: str = None,
    ) -> None:
        SharedStorage.delete_for_opensandbox(
            scripts_volume_id,
            data_volume_id,
            tools_volume_id,
            cls._get_global_config(),
        )

    @classmethod
    async def create_single_sandbox(
        cls, session_id: str, sandbox_type: str, container_config
    ):
        from opensage.sandbox.factory import create_sandbox_class, get_initializer_class

        initializer_class = get_initializer_class(sandbox_type)
        sandbox_class = create_sandbox_class(cls, initializer_class)
        sandbox_instance = sandbox_class(
            container_config,
            session_id=session_id,
            backend_type=cls.backend_type,
            sandbox_type=sandbox_type,
        )
        sandbox_instance._create_remote_sandbox()
        sandbox_instance._using_cached = container_config.using_cached
        return sandbox_type, sandbox_instance

    @staticmethod
    async def _run_initializer_with_tracking(
        sandbox_type: str,
        sandbox_instance: "OpenSandboxSandbox",
        init_coro,
    ) -> None:
        final_state: Optional[SandboxState] = None
        sandboxes = None
        opensage_session_id = getattr(sandbox_instance, "opensage_session_id", None)
        if opensage_session_id:
            try:
                from opensage.session.opensage_session import get_opensage_session

                sandboxes = get_opensage_session(opensage_session_id).sandboxes
            except Exception as exc:
                logger.warning(
                    "Failed to retrieve sandbox manager for session %s: %s",
                    opensage_session_id,
                    exc,
                )
        try:
            await init_coro
        except Exception as exc:
            final_state = SandboxState.ERROR
            setattr(sandbox_instance, "state", final_state)
            if sandboxes:
                try:
                    sandboxes.get_sandbox(sandbox_type).state = final_state
                except Exception:
                    pass
            logger.error(
                "sandbox '%s' (session %s) state=%s - Initialization failed: %s",
                sandbox_type,
                opensage_session_id,
                final_state.value,
                exc,
                exc_info=exc,
            )
            raise
        else:
            final_state = SandboxState.READY
            setattr(sandbox_instance, "state", final_state)
            if sandboxes:
                try:
                    sandboxes.get_sandbox(sandbox_type).state = final_state
                except Exception:
                    pass
        finally:
            state_value = final_state.value if final_state else "unknown"
            logger.info(
                "sandbox '%s' (session %s) state=%s - Initialization finished",
                sandbox_type,
                opensage_session_id,
                state_value,
            )

    @classmethod
    async def launch_all_sandboxes(
        cls,
        session_id: str,
        sandbox_configs: dict,
        shared_volume_id: str = None,
        scripts_volume_id: str = None,
        tools_volume_id: str = None,
    ) -> dict:
        async def launch_concurrent():
            tasks = [
                cls.create_single_sandbox(session_id, sandbox_type, container_config)
                for sandbox_type, container_config in sandbox_configs.items()
            ]
            return dict(await asyncio.gather(*tasks))

        sandbox_instances = await launch_concurrent()
        from opensage.session.opensage_session import get_opensage_session

        config = get_opensage_session(session_id).config
        cls._update_service_ports(config, sandbox_instances)
        return sandbox_instances

    @classmethod
    async def initialize_all_sandboxes(
        cls,
        sandbox_instances: dict[str, BaseSandbox],
        *,
        continue_on_error: bool = False,
    ) -> dict:
        if not sandbox_instances:
            logger.warning("No sandbox instances to initialize")
            return {}

        init_entries = []
        for sandbox_type, sandbox_instance in sandbox_instances.items():

            async def _init_one(instance: BaseSandbox) -> None:
                await instance.async_initialize(sandbox_instances)

            init_entries.append(
                (
                    sandbox_type,
                    cls._run_initializer_with_tracking(
                        sandbox_type,
                        sandbox_instance,
                        _init_one(sandbox_instance),
                    ),
                )
            )

        tasks = [entry[1] for entry in init_entries]
        if continue_on_error:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            result_map = {}
            for (sandbox_type, _), res in zip(init_entries, results):
                result_map[sandbox_type] = res if isinstance(res, Exception) else None
            return result_map

        await asyncio.gather(*tasks)
        return {sandbox_type: None for sandbox_type, _ in init_entries}

    @classmethod
    def cache_sandboxes(
        cls,
        sandbox_instances: dict,
        shared_volume_id: str,
        cache_dir: str,
        task_name: str,
    ) -> dict:
        opensandbox_config = cls._get_global_opensandbox_config()
        cache_dir_path = Path(cache_dir)
        cache_dir_path.mkdir(parents=True, exist_ok=True)

        runtime_type = opensandbox_config.runtime_type
        if runtime_type == "docker":
            runtime_cache = cls._cache_docker_runtime(
                sandbox_instances,
                shared_volume_id,
                cache_dir,
                task_name,
                opensandbox_config,
            )
        elif runtime_type == "kubernetes":
            runtime_cache = cls._cache_kubernetes_runtime(
                sandbox_instances,
                shared_volume_id,
                cache_dir,
                task_name,
                opensandbox_config,
            )
        else:
            raise ValueError(f"Unsupported OpenSandbox runtime_type: {runtime_type}")

        manifest_data = {
            "task_name": task_name,
            "cache_dir": cache_dir,
            "shared_volume_backup": runtime_cache.get("shared_volume_backup"),
            "runtime_type": runtime_type,
            "sandboxes": runtime_cache.get("cached_images", {}),
        }
        manifest_path = cache_dir_path / "opensandbox_cache_manifest.json"
        with manifest_path.open("w", encoding="utf-8") as manifest_file:
            json.dump(manifest_data, manifest_file, indent=2)

        cache_results = {
            "backend": "opensandbox",
            "runtime_type": runtime_type,
            "task_name": task_name,
            "cache_dir": cache_dir,
            "shared_volume_backup": runtime_cache.get("shared_volume_backup"),
            "cached_images": runtime_cache.get("cached_images", {}),
            "errors": runtime_cache.get("errors", []),
            "metadata_path": str(manifest_path),
        }
        os.environ[cls._CACHE_DIR_ENV] = str(cache_dir_path)

        global_manifest_dir = Path.home() / ".cache" / "opensage" / "opensandbox_cache"
        global_manifest_dir.mkdir(parents=True, exist_ok=True)
        global_manifest = (
            global_manifest_dir / f"{cls._normalize_cache_name(task_name)}.json"
        )
        with global_manifest.open("w", encoding="utf-8") as global_file:
            json.dump(manifest_data, global_file, indent=2)
        return cache_results

    @classmethod
    def _cache_docker_runtime(
        cls,
        sandbox_instances: dict,
        shared_volume_id: str,
        cache_dir: str,
        task_name: str,
        opensandbox_config: OpenSandboxConfig,
    ) -> dict:
        RemoteDockerSandbox.set_config(
            SimpleNamespace(
                sandbox=SimpleNamespace(
                    docker_host=opensandbox_config.docker_host,
                    docker_remote_host=opensandbox_config.docker_remote_host,
                )
            )
        )
        fake_instances = {}
        opensandbox_id_by_type = {}
        for sandbox_type, sandbox_instance in sandbox_instances.items():
            opensandbox_id = getattr(sandbox_instance, "opensandbox_id", None)
            if not opensandbox_id:
                continue
            container_id = cls._discover_remote_docker_container_id(opensandbox_id)
            fake_instances[sandbox_type] = SimpleNamespace(container_id=container_id)
            opensandbox_id_by_type[sandbox_type] = opensandbox_id

        cache_result = RemoteDockerSandbox.cache_sandboxes(
            fake_instances,
            shared_volume_id,
            cache_dir,
            task_name,
        )
        for sandbox_type, info in cache_result.get("cached_images", {}).items():
            info["opensandbox_id"] = opensandbox_id_by_type.get(sandbox_type)
        return cache_result

    @classmethod
    def _discover_remote_docker_container_id(cls, opensandbox_id: str) -> str:
        client = RemoteDockerSandbox._get_docker_client()
        containers = client.containers.list(
            all=True,
            filters={"label": f"{cls._OPENSANDBOX_ID_LABEL}={opensandbox_id}"},
        )
        if not containers:
            raise RuntimeError(
                f"No remote Docker container found for OpenSandbox ID {opensandbox_id}"
            )
        return containers[0].id

    @classmethod
    def _cache_kubernetes_runtime(
        cls,
        sandbox_instances: dict,
        shared_volume_id: str,
        cache_dir: str,
        task_name: str,
        opensandbox_config: OpenSandboxConfig,
    ) -> dict:
        fake_instances = {}
        opensandbox_id_by_type = {}
        for sandbox_type, sandbox_instance in sandbox_instances.items():
            opensandbox_id = getattr(sandbox_instance, "opensandbox_id", None)
            if not opensandbox_id:
                continue
            pod_name, container_name = cls._discover_k8s_pod_and_container(
                opensandbox_id,
                opensandbox_config,
                image_hint=getattr(
                    sandbox_instance.container_config_obj, "image", None
                ),
            )
            fake_instances[sandbox_type] = SimpleNamespace(
                pod_name=pod_name,
                container_name=container_name,
                namespace=opensandbox_config.namespace or "default",
                context=opensandbox_config.context,
                kubeconfig=opensandbox_config.kubeconfig,
                container_config_obj=SimpleNamespace(
                    image=getattr(sandbox_instance.container_config_obj, "image", None)
                ),
            )
            opensandbox_id_by_type[sandbox_type] = opensandbox_id

        with _temporary_env(
            {
                K8sSandbox.DEFAULT_NAMESPACE_ENV: opensandbox_config.namespace,
                K8sSandbox.DEFAULT_CONTEXT_ENV: opensandbox_config.context,
                K8sSandbox.DEFAULT_KUBECONFIG_ENV: opensandbox_config.kubeconfig,
            }
        ):
            cache_result = K8sSandbox.cache_sandboxes(
                fake_instances,
                shared_volume_id,
                cache_dir,
                task_name,
            )
        for sandbox_type, info in cache_result.get("cached_images", {}).items():
            info["opensandbox_id"] = opensandbox_id_by_type.get(sandbox_type)
        return cache_result

    @classmethod
    def _discover_k8s_pod_and_container(
        cls,
        opensandbox_id: str,
        opensandbox_config: OpenSandboxConfig,
        *,
        image_hint: Optional[str] = None,
    ) -> tuple[str, str]:
        namespace = opensandbox_config.namespace or "default"
        result = K8sSandbox._run_kubectl_class(
            [
                "get",
                "pods",
                "-l",
                f"{cls._OPENSANDBOX_ID_LABEL}={opensandbox_id}",
                "-o",
                "json",
            ],
            namespace=namespace,
            context=opensandbox_config.context,
            kubeconfig=opensandbox_config.kubeconfig,
            text=True,
        )
        data = json.loads(result.stdout)
        items = data.get("items", [])
        if not items:
            raise RuntimeError(
                f"No Kubernetes Pod found for OpenSandbox ID {opensandbox_id}"
            )

        selected_pod = None
        for item in items:
            if item.get("status", {}).get("phase") == "Running":
                selected_pod = item
                break
        if selected_pod is None:
            selected_pod = items[0]

        pod_name = selected_pod["metadata"]["name"]
        containers = selected_pod.get("spec", {}).get("containers", [])
        if not containers:
            raise RuntimeError(f"Pod {pod_name} has no containers")

        container_name = None
        if image_hint:
            for container in containers:
                if container.get("image") == image_hint:
                    container_name = container.get("name")
                    break
        if container_name is None:
            for container in containers:
                name = container.get("name", "")
                if "egress" not in name:
                    container_name = name
                    break
        if container_name is None:
            container_name = containers[0]["name"]
        return pod_name, container_name

    @classmethod
    def _parse_endpoint_host_port(cls, endpoint: str) -> Optional[tuple[str, int]]:
        if not endpoint:
            return None
        if "://" in endpoint:
            endpoint = endpoint.split("://", 1)[1]
        if "/" in endpoint:
            endpoint = endpoint.split("/", 1)[0]
        if ":" not in endpoint:
            return None
        host, port = endpoint.rsplit(":", 1)
        try:
            return host, int(port)
        except ValueError:
            return None

    @classmethod
    def _update_service_ports(
        cls, config: OpenSageConfig, sandbox_instances: dict[str, "OpenSandboxSandbox"]
    ) -> None:
        if not sandbox_instances:
            return

        for sandbox_type, sandbox_instance in sandbox_instances.items():
            ports = sandbox_instance.container_config_obj.ports or {}
            resolved_ports: dict[int, tuple[str, int]] = {}
            for container_port in ports.keys():
                internal_port = int(str(container_port).split("/")[0])
                try:
                    endpoint = sandbox_instance.sandbox_service.get_sandbox_endpoint(
                        sandbox_instance.opensandbox_id,
                        internal_port,
                        False,
                    )
                    host_port = cls._parse_endpoint_host_port(endpoint.endpoint)
                    if host_port:
                        resolved_ports[internal_port] = host_port
                except Exception as exc:
                    logger.debug(
                        "Failed to resolve endpoint for sandbox %s port %s: %s",
                        sandbox_type,
                        internal_port,
                        exc,
                    )

            if not resolved_ports:
                continue

            if sandbox_type == "neo4j" and config.neo4j:
                bolt_endpoint = resolved_ports.get(7687)
                http_endpoint = resolved_ports.get(7474)
                if bolt_endpoint:
                    config.default_host = bolt_endpoint[0]
                    config.neo4j.bolt_port = bolt_endpoint[1]
                if http_endpoint:
                    config.default_host = http_endpoint[0]
                    config.neo4j.neo4j_http_port = http_endpoint[1]

            if (
                config.mcp
                and config.mcp.services
                and sandbox_type in config.mcp.services
            ):
                first_endpoint = next(iter(resolved_ports.values()))
                service_config = config.mcp.services[sandbox_type]
                service_config._sse_host = first_endpoint[0]
                service_config._sse_port = first_endpoint[1]

    @classmethod
    def checkpoint(cls) -> str:
        """Checkpoint the sandbox."""
        raise NotImplementedError("Checkpoint is not implemented for LocalSandbox")

    @classmethod
    def restore(cls) -> str:
        """Restore the sandbox."""
        raise NotImplementedError("Restore is not implemented for LocalSandbox")
