from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import docker
import pytest
from docker.errors import APIError, ImageNotFound, NotFound

from aigise.config.config_dataclass import AigiseConfig, ContainerConfig, SandboxConfig
from aigise.session import AigiseSessionRegistry, get_aigise_session
from aigise.session.aigise_sandbox_manager import AigiseSandboxManager


@dataclass
class SandboxBackendScenario:
    name: str
    backend: str
    default_image: str = "alpine:latest"

    def ensure_available(self) -> None:  # pragma: no cover - implemented by subclasses
        raise NotImplementedError

    def build_config(self) -> AigiseConfig:
        config = AigiseConfig()
        config.task_name = f"{self.name}_test_task"
        main_config = ContainerConfig(
            image=self.default_image,
            environment={"TEST_ENV": "main"},
            timeout=30,
        )
        worker_config = ContainerConfig(
            image=self.default_image,
            environment={"TEST_ENV": "worker"},
            timeout=30,
        )
        config.sandbox = SandboxConfig(
            default_image=self.default_image,
            backend=self.backend,
            sandboxes={"main": main_config, "worker": worker_config},
        )
        return config

    def generate_session_id(self) -> str:
        return f"test_{self.name}_session_{uuid.uuid4().hex[:8]}"

    def cleanup_shared_volumes(
        self,
        scripts_volume_id: Optional[str],
        data_volume_id: Optional[str],
        config: Optional[AigiseConfig],
    ) -> None:  # pragma: no cover - implemented by subclasses
        raise NotImplementedError

    def cleanup_cached_images(self, cache_result: Optional[dict]) -> None:
        if not cache_result:
            return
        # Fallback no-op for subclasses that do not require cleanup


class NativeScenario(SandboxBackendScenario):
    def __init__(self) -> None:
        super().__init__(name="native", backend="native")

    def ensure_available(self) -> None:
        try:
            docker.from_env().ping()
        except Exception:
            pytest.skip("Docker not available for testing")

    def cleanup_shared_volumes(
        self,
        scripts_volume_id: Optional[str],
        data_volume_id: Optional[str],
        config: Optional[AigiseConfig],
    ) -> None:
        client = docker.from_env()
        for volume_id in [scripts_volume_id, data_volume_id]:
            if not volume_id:
                continue
            try:
                client.volumes.get(volume_id).remove(force=True)
            except NotFound:
                continue
            except APIError:
                continue

    def cleanup_cached_images(self, cache_result: Optional[dict]) -> None:
        if not cache_result:
            return
        cached_images = cache_result.get("cached_images", {}) or {}
        if not cached_images:
            return
        client = docker.from_env()
        for info in cached_images.values():
            image_name = info.get("image_name")
            if not image_name:
                continue
            try:
                client.images.remove(image=image_name, force=True)
            except ImageNotFound:
                continue
            except APIError:
                continue


class K8sScenario(SandboxBackendScenario):
    def __init__(self) -> None:
        super().__init__(name="k8s", backend="k8s")

    def ensure_available(self) -> None:
        if shutil.which("kubectl") is None:
            pytest.skip("kubectl not available for testing")
        try:
            subprocess.run(
                ["kubectl", "version", "--request-timeout=5s"],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="ignore") if exc.stderr else ""
            pytest.skip(f"Kubernetes cluster not reachable: {stderr.strip()}")

    def _resolve_namespace(self, config: Optional[AigiseConfig]) -> str:
        if config and config.sandbox and config.sandbox.sandboxes:
            values = set()
            for container in config.sandbox.sandboxes.values():
                extra = container.extra or {}
                for key in ("namespace", "k8s_namespace"):
                    value = extra.get(key)
                    if value:
                        values.add(value)
            if len(values) == 1:
                return values.pop()
        env_value = os.getenv("AIGISE_K8S_NAMESPACE")
        if env_value:
            return env_value
        return "default"

    def cleanup_shared_volumes(
        self,
        scripts_volume_id: Optional[str],
        data_volume_id: Optional[str],
        config: Optional[AigiseConfig],
    ) -> None:
        namespace = self._resolve_namespace(config)
        for volume_id in [scripts_volume_id, data_volume_id]:
            if not volume_id:
                continue
            subprocess.run(
                [
                    "kubectl",
                    "-n",
                    namespace,
                    "delete",
                    "pvc",
                    volume_id,
                    "--ignore-not-found=true",
                ],
                check=False,
                capture_output=True,
            )
            # Best-effort cleanup of compatibility Docker volume, mirroring backend behaviour
            try:
                client = docker.from_env()
                client.volumes.get(volume_id).remove(force=True)
            except (NotFound, APIError, Exception):
                pass

    def cleanup_cached_images(self, cache_result: Optional[dict]) -> None:
        if not cache_result:
            return
        cached_images = cache_result.get("cached_images", {}) or {}
        if not cached_images:
            return
        client = docker.from_env()
        for info in cached_images.values():
            image_name = info.get("image_name")
            if not image_name:
                continue
            try:
                client.images.remove(image=image_name, force=True)
            except (ImageNotFound, APIError):
                continue
            except Exception:
                continue


SCENARIOS = [
    pytest.param(NativeScenario(), id="native", marks=pytest.mark.native_backend),
    pytest.param(K8sScenario(), id="k8s", marks=pytest.mark.k8s_backend),
]


@pytest.fixture(params=SCENARIOS)
def sandbox_scenario(request) -> SandboxBackendScenario:
    scenario: SandboxBackendScenario = request.param
    scenario.ensure_available()
    return scenario


@pytest.mark.asyncio
async def test_shared_volume_initialization_and_launch(
    sandbox_scenario: SandboxBackendScenario,
):
    config = sandbox_scenario.build_config()
    session_id = sandbox_scenario.generate_session_id()
    scripts_volume_id: Optional[str] = None
    shared_volume_id: Optional[str] = None
    manager: Optional[AigiseSandboxManager] = None

    with tempfile.TemporaryDirectory() as temp_dir:
        test_file_path = Path(temp_dir) / "shared_test_file.txt"
        test_file_path.write_text("This is shared data for all sandboxes")
        nested_dir = Path(temp_dir) / "subdir"
        nested_dir.mkdir()
        (nested_dir / "nested_file.txt").write_text("Nested shared data")
        config.sandbox.absolute_shared_data_path = temp_dir

        # Use get_aigise_session to create session
        aigise_session = get_aigise_session(session_id)
        aigise_session.config = config
        manager = aigise_session.sandboxes
        try:
            manager.initialize_shared_volumes()
            scripts_volume_id = manager._scripts_volume_id
            shared_volume_id = manager.get_shared_volume()
            assert scripts_volume_id is not None
            assert shared_volume_id is not None

            await manager.launch_all_sandboxes()
            if sandbox_scenario.backend == "native":
                assert len(manager._sandboxes) == 3
                # there is a placeholder sandbox
                assert "_placeholder" in manager._sandboxes
            else:
                assert len(manager._sandboxes) == 2

            main_sandbox = manager._sandboxes["main"]
            worker_sandbox = manager._sandboxes["worker"]

            output, exit_code = main_sandbox.run_command_in_container(
                "cat /shared/shared_test_file.txt"
            )
            assert exit_code == 0, f"Main sandbox failed to read shared file: {output}"
            assert "This is shared data for all sandboxes" in output

            output, exit_code = worker_sandbox.run_command_in_container(
                "cat /shared/shared_test_file.txt"
            )
            assert exit_code == 0, (
                f"Worker sandbox failed to read shared file: {output}"
            )
            assert "This is shared data for all sandboxes" in output

            output, exit_code = main_sandbox.run_command_in_container(
                "cat /shared/subdir/nested_file.txt"
            )
            assert exit_code == 0
            assert "Nested shared data" in output

            main_sandbox.run_command_in_container(
                "echo 'Written by main sandbox' > /shared/main_created.txt"
            )
            output, exit_code = worker_sandbox.run_command_in_container(
                "cat /shared/main_created.txt"
            )
            assert exit_code == 0
            assert "Written by main sandbox" in output

            worker_sandbox.run_command_in_container(
                "echo 'Written by worker sandbox' > /shared/worker_created.txt"
            )
            output, exit_code = main_sandbox.run_command_in_container(
                "cat /shared/worker_created.txt"
            )
            assert exit_code == 0
            assert "Written by worker sandbox" in output
        finally:
            if manager:
                manager.cleanup()
    sandbox_scenario.cleanup_shared_volumes(scripts_volume_id, shared_volume_id, config)


# @pytest.mark.asyncio
# async def test_cache_shared_volume_and_containers(
#     sandbox_scenario: SandboxBackendScenario,
# ):
#     cache_dir_path = Path(tempfile.mkdtemp(prefix="aigise-cache-"))
#     manager: Optional[AigiseSandboxManager] = None
#     reloaded_manager: Optional[AigiseSandboxManager] = None
#     scripts_volume_id: Optional[str] = None
#     shared_volume_id: Optional[str] = None
#     reloaded_scripts_volume_id: Optional[str] = None
#     reloaded_shared_volume_id: Optional[str] = None
#     cache_result: Optional[dict] = None
#     initial_config = sandbox_scenario.build_config()
#     reloaded_config: Optional[AigiseConfig] = None
#     reloaded_session_id: Optional[str] = None
#     session_id: Optional[str] = None

#     try:
#         with tempfile.TemporaryDirectory(prefix="aigise-shared-") as temp_dir:
#             shared_path = Path(temp_dir)
#             (shared_path / "initial_shared_data.txt").write_text("Initial shared data")
#             initial_config.sandbox.absolute_shared_data_path = temp_dir
#             session_id = sandbox_scenario.generate_session_id()

#             # Use get_aigise_session to create session
#             aigise_session = get_aigise_session(session_id)
#             aigise_session.config = initial_config
#             manager = aigise_session.sandboxes
#             manager.initialize_shared_volumes()
#             scripts_volume_id = manager._scripts_volume_id
#             shared_volume_id = manager.get_shared_volume()
#             assert scripts_volume_id is not None
#             assert shared_volume_id is not None

#             await manager.launch_all_sandboxes()
#             main_sandbox = manager._sandboxes["main"]
#             worker_sandbox = manager._sandboxes["worker"]

#             main_sandbox.run_command_in_container(
#                 "echo 'Main container data' > /tmp/main_container_file.txt"
#             )
#             worker_sandbox.run_command_in_container(
#                 "echo 'Worker container data' > /tmp/worker_container_file.txt"
#             )
#             main_sandbox.run_command_in_container(
#                 "echo 'Data written by main to shared volume' > /shared/runtime_shared_file.txt"
#             )
#             worker_sandbox.run_command_in_container(
#                 "echo 'Data written by worker to shared volume' > /shared/worker_runtime_file.txt"
#             )

#             cache_result = manager.cache_sandboxes(cache_dir=str(cache_dir_path))
#             assert "cached_images" in cache_result
#             assert "shared_volume_backup" in cache_result
#             volume_backup_path = cache_result["shared_volume_backup"]
#             assert volume_backup_path and os.path.exists(volume_backup_path)

#             with tarfile.open(volume_backup_path, "r:gz") as tar:
#                 tar_members = [member.name.lstrip("./") for member in tar.getmembers()]
#             assert "initial_shared_data.txt" in tar_members
#             assert "runtime_shared_file.txt" in tar_members
#             assert "worker_runtime_file.txt" in tar_members

#         # Prepare for reload using cached artefacts
#         # Cleanup first session from registry
#         AigiseSessionRegistry.remove_session(session_id)
#         manager = None
#         sandbox_scenario.cleanup_shared_volumes(
#             scripts_volume_id, shared_volume_id, initial_config
#         )
#         scripts_volume_id = None
#         shared_volume_id = None

#         reloaded_config = sandbox_scenario.build_config()
#         reloaded_config.sandbox.absolute_shared_data_path = str(cache_dir_path)
#         reloaded_session_id = sandbox_scenario.generate_session_id()

#         # Use get_aigise_session to create reloaded session
#         reloaded_aigise_session = get_aigise_session(reloaded_session_id)
#         reloaded_aigise_session.config = reloaded_config
#         reloaded_manager = reloaded_aigise_session.sandboxes
#         reloaded_manager.load_sandbox_caches_to_config()
#         reloaded_manager.initialize_shared_volumes()
#         reloaded_scripts_volume_id = reloaded_manager._scripts_volume_id
#         reloaded_shared_volume_id = reloaded_manager.get_shared_volume()
#         assert reloaded_scripts_volume_id is not None
#         assert reloaded_shared_volume_id is not None

#         await reloaded_manager.launch_all_sandboxes()
#         reloaded_main = reloaded_manager._sandboxes["main"]
#         reloaded_worker = reloaded_manager._sandboxes["worker"]

#         output, exit_code = reloaded_main.run_command_in_container(
#             "cat /tmp/main_container_file.txt"
#         )
#         assert exit_code == 0, output
#         assert "Main container data" in output

#         output, exit_code = reloaded_worker.run_command_in_container(
#             "cat /tmp/worker_container_file.txt"
#         )
#         assert exit_code == 0, output
#         assert "Worker container data" in output

#         output, exit_code = reloaded_main.run_command_in_container(
#             "cat /shared/runtime_shared_file.txt"
#         )
#         assert exit_code == 0, output
#         assert "Data written by main to shared volume" in output

#         output, exit_code = reloaded_worker.run_command_in_container(
#             "cat /shared/worker_runtime_file.txt"
#         )
#         assert exit_code == 0, output
#         assert "Data written by worker to shared volume" in output
#     finally:
#         # Cleanup sessions from registry (if they were created)
#         if session_id in AigiseSessionRegistry._sessions:
#             AigiseSessionRegistry.remove_session(session_id)
#         if reloaded_session_id in AigiseSessionRegistry._sessions:
#             AigiseSessionRegistry.remove_session(reloaded_session_id)
#         sandbox_scenario.cleanup_shared_volumes(
#             scripts_volume_id, shared_volume_id, initial_config
#         )
#         sandbox_scenario.cleanup_shared_volumes(
#             reloaded_scripts_volume_id, reloaded_shared_volume_id, reloaded_config
#         )
#         sandbox_scenario.cleanup_cached_images(cache_result)
#         shutil.rmtree(cache_dir_path, ignore_errors=True)


if __name__ == "__main__":  # pragma: no cover - manual execution helper
    import sys

    pytest.main([__file__] + sys.argv[1:])
