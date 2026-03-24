"""Namespace sandbox backend — thin adapter over agentdocker-lite.

Delegates all isolation (namespaces, overlayfs/btrfs, cgroups, seccomp,
persistent shell) to the ``agentdocker_lite`` package while implementing
the OpenSage :class:`BaseSandbox` interface.

Configuration::

    [sandbox]
    backend = "agentdocker-lite"

    [sandbox.sandboxes.main]
    image = "/base-images/ubuntu-22.04"   # rootfs dir or Docker image
    working_dir = "/workspace"

    [sandbox.sandboxes.main.extra]
    fs_backend = "btrfs"                  # "overlayfs" (default) or "btrfs"
    env_base_dir = "/tmp/opensage_ns"       # workspace base directory
    cpu_max = "50000 100000"              # cgroup cpu.max
    memory_max = "536870912"              # cgroup memory.max (512 MB)
    pids_max = "256"                      # cgroup pids.max
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Awaitable, Optional

from agentdocker_lite import Sandbox as _make_sandbox
from agentdocker_lite import SandboxConfig as _SandboxConfig

from opensage.config.config_dataclass import ContainerConfig
from opensage.sandbox.base_sandbox import BaseSandbox, SandboxState

logger = logging.getLogger(__name__)


def _container_config_to_sandbox_config(
    cc: ContainerConfig,
    session_id: str | None = None,
    sandbox_type: str | None = None,
) -> tuple[_SandboxConfig, str]:
    """Convert OpenSage ContainerConfig → agentdocker-lite SandboxConfig.

    Returns (sandbox_config, sandbox_name).
    """
    extra = cc.extra or {}

    name = f"{session_id}_{sandbox_type}" if session_id else (sandbox_type or "default")

    cfg = _SandboxConfig(
        image=cc.image or "",
        working_dir=cc.working_dir or "/",
        environment={k: str(v) for k, v in (cc.environment or {}).items()},
        volumes=list(cc.volumes or []),
        fs_backend=extra.get("fs_backend", "overlayfs"),
        env_base_dir=extra.get("env_base_dir", "/tmp/opensage_ns"),
        rootfs_cache_dir=extra.get("rootfs_cache_dir", "/tmp/opensage_rootfs_cache"),
        cpu_max=extra.get("cpu_max"),
        memory_max=extra.get("memory_max"),
        pids_max=extra.get("pids_max"),
    )
    return cfg, name


class AgentDockerLiteSandbox(BaseSandbox):
    """OpenSage adapter over ``agentdocker_lite.Sandbox``.

    Translates :class:`ContainerConfig` to :class:`SandboxConfig` and
    delegates sandbox operations to the underlying agentdocker-lite instance.
    """

    backend_type = "agentdocker-lite"
    DEFAULT_ENV_BASE_DIR = "/tmp/agentdocker_lite"

    def __init__(
        self,
        container_config: ContainerConfig,
        opensage_session_id: str = None,
        backend_type: str = None,
        sandbox_type: str = None,
    ):
        assert backend_type == "agentdocker-lite", (
            f"AgentDockerLiteSandbox requires backend_type='agentdocker-lite', got {backend_type!r}"
        )
        super().__init__(
            container_config, opensage_session_id, backend_type, sandbox_type
        )

        cfg, name = _container_config_to_sandbox_config(
            container_config, opensage_session_id, sandbox_type
        )
        t0 = time.monotonic()
        self._inner = _make_sandbox(cfg, name)
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "AgentDockerLiteSandbox ready (%.1fms): name=%s fs=%s rootfs=%s",
            elapsed_ms,
            name,
            cfg.fs_backend,
            self._rootfs,
        )

    # -- expose internals for tests & caching ----------------------------- #

    @property
    def _rootfs(self) -> Path:
        return self._inner.rootfs

    # -- agentdocker-lite API pass-through ---------------------------------- #

    def save_as_image(self, image_name: str) -> None:
        """Save current sandbox state as a Docker image."""
        self._inner.save_as_image(image_name)

    # -- BaseSandbox: command execution ------------------------------------ #

    def run_command_in_container(
        self, command: str | list[str], timeout: int | None = None
    ) -> tuple[str, int]:
        return self._inner.run(command, timeout=timeout)

    # -- BaseSandbox: file operations -------------------------------------- #

    def copy_file_from_container(self, src_path: str, dst_path: str):
        self._inner.copy_from(src_path, dst_path)

    def copy_file_to_container(self, local_path: str, container_path: str):
        self._inner.copy_to(local_path, container_path)

    def copy_directory_from_container(self, src_path: str, dst_path: str):
        host_src = self._rootfs / src_path.lstrip("/")
        if not host_src.exists():
            raise ValueError(
                f"Path {src_path} does not exist in the sandbox environment."
            )
        if os.path.exists(dst_path):
            shutil.rmtree(dst_path)
        shutil.copytree(str(host_src), dst_path, dirs_exist_ok=True)

    def extract_file_from_container(self, filepath: str) -> str:
        return self._inner.read_file(filepath)

    def extract_file_from_container_bytes(self, filepath: str) -> bytes:
        host_path = self._rootfs / filepath.lstrip("/")
        if not host_path.exists():
            raise FileNotFoundError(
                f"File {filepath} does not exist in the sandbox environment."
            )
        return host_path.read_bytes()

    def get_work_dir(self) -> str:
        return self.container_config_obj.working_dir or "/"

    # -- RL fast-path: environment reset ----------------------------------- #

    def reset_environment(self):
        t0 = time.monotonic()
        self._inner.reset()
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.debug("Environment reset (%.3fms): %s", elapsed_ms, self._rootfs)

    # -- cleanup ----------------------------------------------------------- #

    def delete_container(self, max_wait: int = 10):
        t0 = time.monotonic()
        self._inner.delete()
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info("Deleted sandbox env (%.1fms): %s", elapsed_ms, self._rootfs)

    def __del__(self):
        pass  # agentdocker-lite handles its own __del__

    # ------------------------------------------------------------------ #
    #  Class methods — shared volume management                          #
    # ------------------------------------------------------------------ #

    @classmethod
    def create_shared_volume(
        cls,
        volume_name_prefix: str,
        init_data_path: Path = None,
        tools_top_roots: set[str] | None = None,
    ) -> tuple[str, str, str]:
        import subprocess

        t0 = time.monotonic()

        from opensage.utils.bash_tools_staging import build_bash_tools_staging_dir
        from opensage.utils.project_info import SRC_PATH

        base = Path(cls.DEFAULT_ENV_BASE_DIR) / "shared_volumes"
        base.mkdir(parents=True, exist_ok=True)

        scripts_dir = base / f"{volume_name_prefix}_sandbox_scripts"
        scripts_src = SRC_PATH / "sandbox_scripts"
        if scripts_dir.exists():
            shutil.rmtree(scripts_dir)
        if scripts_src.exists():
            shutil.copytree(scripts_src, scripts_dir)
        else:
            scripts_dir.mkdir(parents=True)

        data_dir = base / f"{volume_name_prefix}_shared"
        if data_dir.exists():
            shutil.rmtree(data_dir)
        if init_data_path and init_data_path.exists():
            shutil.copytree(init_data_path, data_dir)
        else:
            data_dir.mkdir(parents=True)
        subprocess.run(["chmod", "-R", "777", str(data_dir)], capture_output=True)

        tools_dir = base / f"{volume_name_prefix}_bash_tools"
        if tools_dir.exists():
            shutil.rmtree(tools_dir)
        with build_bash_tools_staging_dir(roots_to_copy=tools_top_roots) as staging:
            shutil.copytree(staging, tools_dir)
        subprocess.run(["chmod", "-R", "777", str(tools_dir)], capture_output=True)

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "Created shared volumes (%.1fms): scripts=%s data=%s tools=%s",
            elapsed_ms,
            scripts_dir,
            data_dir,
            tools_dir,
        )
        return str(scripts_dir), str(data_dir), str(tools_dir)

    @classmethod
    async def create_single_sandbox(
        cls, session_id: str, sandbox_type: str, container_config: ContainerConfig
    ) -> tuple[str, "AgentDockerLiteSandbox"]:
        from opensage.sandbox.factory import create_sandbox_class, get_initializer_class

        t0 = time.monotonic()
        initializer_class = get_initializer_class(sandbox_type)
        sandbox_class = create_sandbox_class(cls, initializer_class)

        sandbox_instance = sandbox_class(
            container_config,
            session_id=session_id,
            backend_type=cls.backend_type,
            sandbox_type=sandbox_type,
        )
        sandbox_instance._using_cached = container_config.using_cached

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.debug("Created sandbox '%s' (%.1fms)", sandbox_type, elapsed_ms)
        return sandbox_type, sandbox_instance

    @classmethod
    async def launch_all_sandboxes(
        cls,
        session_id: str,
        sandbox_configs: dict,
        shared_volume_id: str = None,
        scripts_volume_id: str = None,
        tools_volume_id: str = None,
    ) -> dict:
        t0 = time.monotonic()
        sandbox_instances: dict[str, AgentDockerLiteSandbox] = {}

        for sandbox_type, config in sandbox_configs.items():
            extra_volumes: list[str] = []
            if scripts_volume_id:
                extra_volumes.append(f"{scripts_volume_id}:/sandbox_scripts:ro")
            if shared_volume_id:
                extra_volumes.append(f"{shared_volume_id}:/shared:rw")
            if tools_volume_id:
                extra_volumes.append(f"{tools_volume_id}:/bash_tools:rw")

            if config.volumes is None:
                config.volumes = []
            config.volumes = extra_volumes + list(config.volumes)

            sandbox_type_key, sandbox_instance = await cls.create_single_sandbox(
                session_id, sandbox_type, config
            )
            sandbox_instances[sandbox_type_key] = sandbox_instance

        # Namespace mode shares host network — services are at 127.0.0.1
        try:
            from opensage.session.opensage_session import get_opensage_session

            opensage_session = get_opensage_session(session_id)
            opensage_session.config.default_host = "127.0.0.1"
        except Exception:
            pass

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "Launched %d agentdocker-lite sandboxes (%.1fms): %s",
            len(sandbox_instances),
            elapsed_ms,
            list(sandbox_instances.keys()),
        )
        return sandbox_instances

    @classmethod
    async def initialize_all_sandboxes(
        cls,
        sandbox_instances: dict,
        *,
        continue_on_error: bool = False,
    ) -> dict:
        if not sandbox_instances:
            return {}

        t0 = time.monotonic()
        init_entries = []

        for sandbox_type, sandbox_instance in sandbox_instances.items():

            async def _init_one(instance: "AgentDockerLiteSandbox") -> None:
                if getattr(instance, "_using_cached", False):
                    await instance.ensure_ready()
                else:
                    await instance.async_initialize(sandbox_instances)

            timeout_seconds = 3600
            container_cfg = getattr(sandbox_instance, "container_config_obj", None)
            if container_cfg and getattr(container_cfg, "extra", None):
                try:
                    timeout_seconds = int(
                        container_cfg.extra.get("initializer_timeout_sec", 3600)
                    )
                except Exception:
                    timeout_seconds = 3600

            init_entries.append(
                (
                    sandbox_type,
                    cls._run_initializer_with_tracking(
                        sandbox_type,
                        sandbox_instance,
                        asyncio.wait_for(
                            _init_one(sandbox_instance), timeout=timeout_seconds
                        ),
                    ),
                )
            )

        tasks = [entry[1] for entry in init_entries]

        if continue_on_error:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            result_map = {}
            for (sandbox_type, _), res in zip(init_entries, results):
                if isinstance(res, Exception):
                    logger.error("Init failed for '%s': %s", sandbox_type, res)
                    result_map[sandbox_type] = res
                else:
                    result_map[sandbox_type] = None
            return result_map

        await asyncio.gather(*tasks)
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "Initialized %d sandboxes (%.1fms)",
            len(sandbox_instances),
            elapsed_ms,
        )
        return {sandbox_type: None for sandbox_type, _ in init_entries}

    @staticmethod
    async def _run_initializer_with_tracking(
        sandbox_type: str,
        sandbox_instance: "AgentDockerLiteSandbox",
        init_coro: Awaitable[None],
    ) -> None:
        t0 = time.monotonic()
        final_state: Optional[SandboxState] = None
        sandboxes = None
        opensage_session_id = getattr(sandbox_instance, "opensage_session_id", None)

        if opensage_session_id:
            try:
                from opensage.session.opensage_session import get_opensage_session

                sandboxes = get_opensage_session(opensage_session_id).sandboxes
            except Exception:
                pass

        try:
            await init_coro
        except Exception as exc:
            final_state = SandboxState.ERROR
            sandbox_instance.state = final_state
            if sandboxes:
                try:
                    sandboxes.get_sandbox(sandbox_type).state = final_state
                except Exception:
                    pass
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.error(
                "sandbox '%s' state=%s — init FAILED (%.1fms): %s",
                sandbox_type,
                final_state.value,
                elapsed_ms,
                exc,
                exc_info=exc,
            )
            raise
        else:
            final_state = SandboxState.READY
            sandbox_instance.state = final_state
            if sandboxes:
                try:
                    sandboxes.get_sandbox(sandbox_type).state = final_state
                except Exception:
                    pass
        finally:
            state_value = final_state.value if final_state else "unknown"
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                "sandbox '%s' state=%s — init done (%.1fms)",
                sandbox_type,
                state_value,
                elapsed_ms,
            )

    # ------------------------------------------------------------------ #
    #  Caching                                                           #
    # ------------------------------------------------------------------ #

    @classmethod
    def cache_sandboxes(
        cls,
        sandbox_instances: dict,
        shared_volume_id: str,
        cache_dir: str,
        task_name: str,
    ) -> dict:
        """Cache sandbox state as Docker images via save_as_image().

        Each sandbox is exported as a Docker image named
        ``<task_name>_sandbox_<sandbox_type>:cached``.  The image works
        with both ``docker run`` and ``SandboxConfig(image=...)``.
        """
        t0 = time.monotonic()
        results: dict[str, str] = {}

        for sandbox_type, instance in sandbox_instances.items():
            image_name = f"{task_name}_sandbox_{sandbox_type}:cached"
            try:
                instance.save_as_image(image_name)
                results[sandbox_type] = image_name
            except Exception as e:
                logger.warning("Failed to cache %s: %s", sandbox_type, e)

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info("Cache complete (%.1fms): %d sandboxes", elapsed_ms, len(results))
        return results

    @classmethod
    def delete_shared_volumes(
        cls,
        scripts_volume_id: str = None,
        data_volume_id: str = None,
        tools_volume_id: str = None,
    ) -> None:
        for vol_path in (scripts_volume_id, data_volume_id, tools_volume_id):
            if vol_path and Path(vol_path).exists():
                shutil.rmtree(vol_path, ignore_errors=True)
                logger.info("Deleted shared volume: %s", vol_path)

    @classmethod
    def checkpoint(cls) -> str:
        """Checkpoint is not supported for agentdocker-lite backend."""
        raise NotImplementedError(
            "Checkpoint is not implemented for AgentDockerLiteSandbox"
        )

    @classmethod
    def restore(cls) -> str:
        """Restore is not supported for agentdocker-lite backend."""
        raise NotImplementedError(
            "Restore is not implemented for AgentDockerLiteSandbox"
        )
