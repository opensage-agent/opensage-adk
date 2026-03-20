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

from opensage.config.config_dataclass import (
    ContainerConfig,
    OpenSageConfig,
    SandboxConfig,
)
from opensage.sandbox.base_sandbox import SandboxState
from opensage.session import OpenSageSessionRegistry, get_opensage_session
from opensage.session.opensage_sandbox_manager import OpenSageSandboxManager


@dataclass
class SandboxBackendScenario:
    name: str
    backend: str
    default_image: str = "ubuntu:20.04"

    def ensure_available(self) -> None:  # pragma: no cover - implemented by subclasses
        raise NotImplementedError

    def build_config(self) -> OpenSageConfig:
        config = OpenSageConfig()
        config.task_name = f"{self.name}_test_task"
        # For tests that involve attaching to the "main" sandbox, we want the
        # main container to have python3 and the neo4j python package available
        # (required by MainInitializer). We achieve this by building a dedicated
        # main image from the project Dockerfile.
        main_config = ContainerConfig(
            image=f"{self.default_image}_main",
            project_relative_dockerfile_path="src/opensage/templates/dockerfiles/main/Dockerfile",
            build_args={"BASE_IMAGE": self.default_image},
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
        config: Optional[OpenSageConfig],
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
        config: Optional[OpenSageConfig],
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

    def build_config(self) -> OpenSageConfig:
        # Start from base config (backend=k8s, main/worker containers)
        config = super().build_config()
        # Inject global tolerations for tests to allow scheduling on tainted single-node clusters
        config.sandbox.tolerations = [
            {
                "key": "node.kubernetes.io/disk-pressure",
                "operator": "Exists",
                "effect": "NoSchedule",
            },
            {
                "key": "node.kubernetes.io/disk-pressure",
                "operator": "Exists",
                "effect": "NoExecute",
            },
            {
                "key": "node-role.kubernetes.io/control-plane",
                "operator": "Exists",
                "effect": "NoSchedule",
            },
            {
                "key": "node-role.kubernetes.io/master",
                "operator": "Exists",
                "effect": "NoSchedule",
            },
        ]
        return config

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

    def _resolve_namespace(self, config: Optional[OpenSageConfig]) -> str:
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
        env_value = os.getenv("OPENSAGE_K8S_NAMESPACE")
        if env_value:
            return env_value
        return "default"

    def cleanup_shared_volumes(
        self,
        scripts_volume_id: Optional[str],
        data_volume_id: Optional[str],
        config: Optional[OpenSageConfig],
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
    pytest.param(
        K8sScenario(),
        id="k8s",
        marks=[
            pytest.mark.k8s_backend,
            pytest.mark.skip(reason="temporarily skipped: unstable k8s env"),
        ],
    ),
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
    manager: Optional[OpenSageSandboxManager] = None

    with tempfile.TemporaryDirectory() as temp_dir:
        test_file_path = Path(temp_dir) / "shared_test_file.txt"
        test_file_path.write_text("This is shared data for all sandboxes")
        nested_dir = Path(temp_dir) / "subdir"
        nested_dir.mkdir()
        (nested_dir / "nested_file.txt").write_text("Nested shared data")
        config.sandbox.absolute_shared_data_path = temp_dir

        # Use get_opensage_session to create session
        opensage_session = get_opensage_session(session_id)
        opensage_session.config = config
        manager = opensage_session.sandboxes
        try:
            manager.initialize_shared_volumes()
            scripts_volume_id = manager._scripts_volume_id
            shared_volume_id = manager.get_shared_volume()
            assert scripts_volume_id is not None
            assert shared_volume_id is not None

            await manager.launch_all_sandboxes()
            await manager.initialize_all_sandboxes()
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


@pytest.mark.asyncio
async def test_attach_sandbox_native_docker(sandbox_scenario: SandboxBackendScenario):
    if sandbox_scenario.backend != "native":
        pytest.skip("This test only runs for native backend")

    config_src = sandbox_scenario.build_config()
    session_id_src = sandbox_scenario.generate_session_id()
    scripts_volume_id: Optional[str] = None
    shared_volume_id: Optional[str] = None

    # Create source session and launch containers to attach to
    opensage_session_src = get_opensage_session(session_id_src)
    opensage_session_src.config = config_src
    mgr_src: OpenSageSandboxManager = opensage_session_src.sandboxes

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "hello.txt").write_text("hi")
            config_src.sandbox.absolute_shared_data_path = temp_dir

            mgr_src.initialize_shared_volumes()
            scripts_volume_id = mgr_src._scripts_volume_id
            shared_volume_id = mgr_src.get_shared_volume()

            await mgr_src.launch_all_sandboxes()
            await mgr_src.initialize_all_sandboxes()

            main_src = mgr_src._sandboxes["main"]
            worker_src = mgr_src._sandboxes["worker"]
            assert hasattr(main_src, "container_id")
            assert hasattr(worker_src, "container_id")
            main_id = main_src.container_id
            worker_id = worker_src.container_id
            assert main_id and worker_id

            # Create destination session and attach
            config_dst = sandbox_scenario.build_config()
            session_id_dst = sandbox_scenario.generate_session_id()
            opensage_session_dst = get_opensage_session(session_id_dst)
            opensage_session_dst.config = config_dst
            mgr_dst: OpenSageSandboxManager = opensage_session_dst.sandboxes

            await mgr_dst.attach_sandbox("main", container_id=main_id)
            await mgr_dst.attach_sandbox("worker", container_id=worker_id)
            assert mgr_dst.get_sandbox("main").state == SandboxState.READY
            assert mgr_dst.get_sandbox("worker").state == SandboxState.READY

            out, code = mgr_dst.get_sandbox("main").run_command_in_container(
                "echo attached"
            )
            assert code == 0 and "attached" in out
    finally:
        try:
            mgr_src.cleanup()
        except Exception:
            pass
        OpenSageSessionRegistry.remove_session(session_id_src)
        # best-effort cleanup for created volumes
        try:
            client = docker.from_env()
            for vol in [scripts_volume_id, shared_volume_id]:
                if not vol:
                    continue
                try:
                    client.volumes.get(vol).remove(force=True)
                except (NotFound, APIError, Exception):
                    pass
        except Exception:
            pass


@pytest.mark.asyncio
async def test_attach_sandbox_k8s(sandbox_scenario: SandboxBackendScenario):
    if sandbox_scenario.backend != "k8s":
        pytest.skip("This test only runs for k8s backend")

    config_src = sandbox_scenario.build_config()
    session_id_src = sandbox_scenario.generate_session_id()

    # Create source session and launch pods to attach to
    opensage_session_src = get_opensage_session(session_id_src)
    opensage_session_src.config = config_src
    mgr_src: OpenSageSandboxManager = opensage_session_src.sandboxes

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "hello.txt").write_text("hi")
            config_src.sandbox.absolute_shared_data_path = temp_dir

            mgr_src.initialize_shared_volumes()
            await mgr_src.launch_all_sandboxes()
            await mgr_src.initialize_all_sandboxes()

            main_src = mgr_src._sandboxes["main"]
            worker_src = mgr_src._sandboxes["worker"]
            pod_main = getattr(main_src, "pod_name", None)
            ctn_main = getattr(main_src, "container_name", None)
            pod_worker = getattr(worker_src, "pod_name", None)
            ctn_worker = getattr(worker_src, "container_name", None)
            assert pod_main and ctn_main and pod_worker and ctn_worker

            # Create destination session and attach
            config_dst = sandbox_scenario.build_config()
            session_id_dst = sandbox_scenario.generate_session_id()
            opensage_session_dst = get_opensage_session(session_id_dst)
            opensage_session_dst.config = config_dst
            mgr_dst: OpenSageSandboxManager = opensage_session_dst.sandboxes

            await mgr_dst.attach_sandbox(
                "main", pod_name=pod_main, container_name=ctn_main
            )
            await mgr_dst.attach_sandbox(
                "worker", pod_name=pod_worker, container_name=ctn_worker
            )
            assert mgr_dst.get_sandbox("main").state == SandboxState.READY
            assert mgr_dst.get_sandbox("worker").state == SandboxState.READY

            out, code = mgr_dst.get_sandbox("main").run_command_in_container(
                "echo attached"
            )
            assert code == 0 and "attached" in out
    finally:
        try:
            mgr_src.cleanup()
        except Exception:
            pass
        OpenSageSessionRegistry.remove_session(session_id_src)


# @pytest.mark.asyncio
# async def test_cache_shared_volume_and_containers(
#     sandbox_scenario: SandboxBackendScenario,
# ):
#     cache_dir_path = Path(tempfile.mkdtemp(prefix="opensage-cache-"))
#     manager: Optional[OpenSageSandboxManager] = None
#     reloaded_manager: Optional[OpenSageSandboxManager] = None
#     scripts_volume_id: Optional[str] = None
#     shared_volume_id: Optional[str] = None
#     reloaded_scripts_volume_id: Optional[str] = None
#     reloaded_shared_volume_id: Optional[str] = None
#     cache_result: Optional[dict] = None
#     initial_config = sandbox_scenario.build_config()
#     reloaded_config: Optional[OpenSageConfig] = None
#     reloaded_session_id: Optional[str] = None
#     session_id: Optional[str] = None

#     try:
#         with tempfile.TemporaryDirectory(prefix="opensage-shared-") as temp_dir:
#             shared_path = Path(temp_dir)
#             (shared_path / "initial_shared_data.txt").write_text("Initial shared data")
#             initial_config.sandbox.absolute_shared_data_path = temp_dir
#             session_id = sandbox_scenario.generate_session_id()

#             # Use get_opensage_session to create session
#             opensage_session = get_opensage_session(session_id)
#             opensage_session.config = initial_config
#             manager = opensage_session.sandboxes
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
#         OpenSageSessionRegistry.remove_session(session_id)
#         manager = None
#         sandbox_scenario.cleanup_shared_volumes(
#             scripts_volume_id, shared_volume_id, initial_config
#         )
#         scripts_volume_id = None
#         shared_volume_id = None

#         reloaded_config = sandbox_scenario.build_config()
#         reloaded_config.sandbox.absolute_shared_data_path = str(cache_dir_path)
#         reloaded_session_id = sandbox_scenario.generate_session_id()

#         # Use get_opensage_session to create reloaded session
#         reloaded_opensage_session = get_opensage_session(reloaded_session_id)
#         reloaded_opensage_session.config = reloaded_config
#         reloaded_manager = reloaded_opensage_session.sandboxes
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
#         if session_id in OpenSageSessionRegistry._sessions:
#             OpenSageSessionRegistry.remove_session(session_id)
#         if reloaded_session_id in OpenSageSessionRegistry._sessions:
#             OpenSageSessionRegistry.remove_session(reloaded_session_id)
#         sandbox_scenario.cleanup_shared_volumes(
#             scripts_volume_id, shared_volume_id, initial_config
#         )
#         sandbox_scenario.cleanup_shared_volumes(
#             reloaded_scripts_volume_id, reloaded_shared_volume_id, reloaded_config
#         )
#         sandbox_scenario.cleanup_cached_images(cache_result)
#         shutil.rmtree(cache_dir_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_cache_shared_volume_and_containers(
    sandbox_scenario: SandboxBackendScenario,
):
    cache_dir_path = Path(tempfile.mkdtemp(prefix="opensage-cache-"))
    manager: Optional[OpenSageSandboxManager] = None
    reloaded_manager: Optional[OpenSageSandboxManager] = None
    scripts_volume_id: Optional[str] = None
    shared_volume_id: Optional[str] = None
    reloaded_scripts_volume_id: Optional[str] = None
    reloaded_shared_volume_id: Optional[str] = None
    cache_result: Optional[dict] = None
    initial_config = sandbox_scenario.build_config()
    reloaded_config: Optional[OpenSageConfig] = None
    reloaded_session_id: Optional[str] = None
    session_id: Optional[str] = None

    try:
        with tempfile.TemporaryDirectory(prefix="opensage-shared-") as temp_dir:
            shared_path = Path(temp_dir)
            (shared_path / "initial_shared_data.txt").write_text("Initial shared data")
            initial_config.sandbox.absolute_shared_data_path = temp_dir
            session_id = sandbox_scenario.generate_session_id()

            opensage_session = get_opensage_session(session_id)
            opensage_session.config = initial_config
            manager = opensage_session.sandboxes
            manager.initialize_shared_volumes()
            scripts_volume_id = manager._scripts_volume_id
            shared_volume_id = manager.get_shared_volume()
            assert scripts_volume_id is not None
            assert shared_volume_id is not None

            await manager.launch_all_sandboxes()
            await manager.initialize_all_sandboxes()
            main_sandbox = manager._sandboxes["main"]
            worker_sandbox = manager._sandboxes["worker"]

            main_sandbox.run_command_in_container(
                "echo 'Main container data' > /tmp/main_container_file.txt"
            )
            worker_sandbox.run_command_in_container(
                "echo 'Worker container data' > /tmp/worker_container_file.txt"
            )
            main_sandbox.run_command_in_container(
                "echo 'Data written by main to shared volume' > /shared/runtime_shared_file.txt"
            )
            worker_sandbox.run_command_in_container(
                "echo 'Data written by worker to shared volume' > /shared/worker_runtime_file.txt"
            )

            cache_result = manager.cache_sandboxes(cache_dir=str(cache_dir_path))
            assert "cached_images" in cache_result
            assert "shared_volume_backup" in cache_result
            volume_backup_path = cache_result["shared_volume_backup"]
            assert volume_backup_path and os.path.exists(volume_backup_path)

            with tarfile.open(volume_backup_path, "r:gz") as tar:
                tar_members = [member.name.lstrip("./") for member in tar.getmembers()]
            assert "initial_shared_data.txt" in tar_members
            assert "runtime_shared_file.txt" in tar_members
            assert "worker_runtime_file.txt" in tar_members

        OpenSageSessionRegistry.remove_session(session_id)
        manager = None
        sandbox_scenario.cleanup_shared_volumes(
            scripts_volume_id, shared_volume_id, initial_config
        )
        scripts_volume_id = None
        shared_volume_id = None

        reloaded_config = sandbox_scenario.build_config()
        reloaded_config.sandbox.absolute_shared_data_path = str(cache_dir_path)
        reloaded_session_id = sandbox_scenario.generate_session_id()

        reloaded_opensage_session = get_opensage_session(reloaded_session_id)
        reloaded_opensage_session.config = reloaded_config
        reloaded_manager = reloaded_opensage_session.sandboxes
        reloaded_manager.load_sandbox_caches_to_config()
        reloaded_manager.initialize_shared_volumes()
        reloaded_scripts_volume_id = reloaded_manager._scripts_volume_id
        reloaded_shared_volume_id = reloaded_manager.get_shared_volume()
        assert reloaded_scripts_volume_id is not None
        assert reloaded_shared_volume_id is not None

        await reloaded_manager.launch_all_sandboxes()
        await reloaded_manager.initialize_all_sandboxes()
        reloaded_main = reloaded_manager._sandboxes["main"]
        reloaded_worker = reloaded_manager._sandboxes["worker"]

        output, exit_code = reloaded_main.run_command_in_container(
            "cat /tmp/main_container_file.txt"
        )
        assert exit_code == 0, output
        assert "Main container data" in output

        output, exit_code = reloaded_worker.run_command_in_container(
            "cat /tmp/worker_container_file.txt"
        )
        assert exit_code == 0, output
        assert "Worker container data" in output

        output, exit_code = reloaded_main.run_command_in_container(
            "cat /shared/runtime_shared_file.txt"
        )
        assert exit_code == 0, output
        assert "Data written by main to shared volume" in output

        output, exit_code = reloaded_worker.run_command_in_container(
            "cat /shared/worker_runtime_file.txt"
        )
        assert exit_code == 0, output
        assert "Data written by worker to shared volume" in output
    finally:
        if session_id in OpenSageSessionRegistry._sessions:
            OpenSageSessionRegistry.remove_session(session_id)
        if reloaded_session_id in OpenSageSessionRegistry._sessions:
            OpenSageSessionRegistry.remove_session(reloaded_session_id)
        sandbox_scenario.cleanup_shared_volumes(
            scripts_volume_id, shared_volume_id, initial_config
        )
        sandbox_scenario.cleanup_shared_volumes(
            reloaded_scripts_volume_id, reloaded_shared_volume_id, reloaded_config
        )
        sandbox_scenario.cleanup_cached_images(cache_result)
        shutil.rmtree(cache_dir_path, ignore_errors=True)


if __name__ == "__main__":  # pragma: no cover - manual execution helper
    import sys

    pytest.main([__file__] + sys.argv[1:])
