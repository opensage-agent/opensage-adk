from __future__ import annotations

import asyncio
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from opensage.config import (
    ContainerConfig,
    OpenSageConfig,
    OpenSandboxConfig,
    SandboxConfig,
)
from opensage.sandbox.opensandbox_sandbox import OpenSandboxSandbox
from opensage.sandbox.shared_storage import SharedStorage
from opensage.session.opensage_sandbox_manager import OpenSageSandboxManager


def _set_backend_config(runtime_type: str = "docker") -> OpenSageConfig:
    config = OpenSageConfig()
    config.task_name = "opensandbox-test-task"
    config.sandbox = SandboxConfig(
        backend="opensandbox",
        sandboxes={"main": ContainerConfig(image="ubuntu:22.04")},
        opensandbox=OpenSandboxConfig(
            runtime_type=runtime_type,
            domain="127.0.0.1:8080",
            protocol="http",
        ),
    )
    OpenSandboxSandbox.set_config(config)
    return config


def _build_archive_bytes(filename: str, content: bytes) -> bytes:
    tar_stream = io.BytesIO()
    with tarfile.open(fileobj=tar_stream, mode="w") as tar:
        tarinfo = tarfile.TarInfo(name=filename)
        tarinfo.size = len(content)
        tar.addfile(tarinfo, io.BytesIO(content))
    tar_stream.seek(0)
    return tar_stream.read()


def test_parse_legacy_mounts_converts_to_pvc_volumes():
    _set_backend_config()
    container_config = ContainerConfig(
        image="ubuntu:22.04",
        volumes=[
            "sess_scripts:/sandbox_scripts:ro",
            "sess_shared:/shared:rw",
            "sess_tools:/bash_tools:rw",
        ],
    )
    sandbox = OpenSandboxSandbox(
        container_config,
        session_id="session-1",
        backend_type="opensandbox",
        sandbox_type="main",
    )

    volumes = sandbox._parse_legacy_mounts_to_opensandbox_volumes()

    assert len(volumes) == 3
    assert volumes[0].pvc.claim_name == "sess_scripts"
    assert volumes[0].mount_path == "/sandbox_scripts"
    assert volumes[0].read_only is True
    assert volumes[1].pvc.claim_name == "sess_shared"
    assert volumes[1].mount_path == "/shared"
    assert volumes[1].read_only is False


def test_parse_legacy_mounts_supports_host_path_sources():
    _set_backend_config()
    container_config = ContainerConfig(
        image="ubuntu:22.04",
        volumes=[
            "/tmp/host:/shared:rw",
            "sess_tools:/bash_tools:ro",
        ],
    )
    sandbox = OpenSandboxSandbox(
        container_config,
        session_id="session-1",
        backend_type="opensandbox",
        sandbox_type="main",
    )

    volumes = sandbox._parse_legacy_mounts_to_opensandbox_volumes()
    assert len(volumes) == 2
    assert volumes[0].host.path == "/tmp/host"
    assert volumes[0].mount_path == "/shared"
    assert volumes[0].read_only is False
    assert volumes[0].pvc is None
    assert volumes[1].pvc.claim_name == "sess_tools"
    assert volumes[1].mount_path == "/bash_tools"
    assert volumes[1].read_only is True


def test_get_work_dir_uses_pwd_command(monkeypatch):
    _set_backend_config()
    sandbox = OpenSandboxSandbox(
        ContainerConfig(image="ubuntu:22.04"),
        session_id="session-1",
        backend_type="opensandbox",
        sandbox_type="main",
    )
    called = {}

    def _fake_run(command, timeout=None):
        called["command"] = command
        return "/workspace\n", 0

    monkeypatch.setattr(sandbox, "run_command_in_container", _fake_run)

    assert sandbox.get_work_dir() == "/workspace"
    assert called["command"] == "pwd"


def test_create_single_sandbox_calls_remote_create(monkeypatch):
    _set_backend_config()
    container_config = ContainerConfig(image="ubuntu:22.04")
    created = {}

    def _fake_create(self):
        self.opensandbox_id = "sbx-123"
        created["called"] = True

    monkeypatch.setattr(OpenSandboxSandbox, "_create_remote_sandbox", _fake_create)

    sandbox_type, sandbox = asyncio.run(
        OpenSandboxSandbox.create_single_sandbox("session-1", "main", container_config)
    )

    assert sandbox_type == "main"
    assert created["called"] is True
    assert sandbox.opensandbox_id == "sbx-123"


def test_shared_storage_dispatches_to_remote_docker(monkeypatch, tmp_path: Path):
    config = _set_backend_config(runtime_type="docker")
    calls = {}

    def _fake_create_shared_volume(session_id, init_data_path, tools_top_roots):
        calls["session_id"] = session_id
        return ("scripts-vol", "shared-vol", "tools-vol")

    monkeypatch.setattr(
        "opensage.sandbox.shared_storage.RemoteDockerSandbox.create_shared_volume",
        _fake_create_shared_volume,
    )

    scripts_id, shared_id, tools_id = SharedStorage.create_for_opensandbox(
        "session-1", tmp_path, None, config
    )

    assert calls["session_id"] == "session-1"
    assert (scripts_id, shared_id, tools_id) == (
        "scripts-vol",
        "shared-vol",
        "tools-vol",
    )


def test_shared_storage_dispatches_to_k8s(monkeypatch, tmp_path: Path):
    config = _set_backend_config(runtime_type="kubernetes")
    calls = {}

    def _fake_create_shared_volume(session_id, init_data_path, tools_top_roots):
        calls["session_id"] = session_id
        return ("scripts-pvc", "shared-pvc", "tools-pvc")

    monkeypatch.setattr(
        "opensage.sandbox.shared_storage.K8sSandbox.create_shared_volume",
        _fake_create_shared_volume,
    )

    scripts_id, shared_id, tools_id = SharedStorage.create_for_opensandbox(
        "session-1", tmp_path, None, config
    )

    assert calls["session_id"] == "session-1"
    assert (scripts_id, shared_id, tools_id) == (
        "scripts-pvc",
        "shared-pvc",
        "tools-pvc",
    )


def test_remote_docker_cache_sandboxes_writes_shared_backup_and_manifest(
    monkeypatch, tmp_path: Path
):
    from opensage.sandbox.remote_docker_sandbox import RemoteDockerSandbox

    fake_client = mock.MagicMock()
    fake_container = mock.MagicMock()
    fake_container.id = "container-123"
    fake_container.commit.return_value = SimpleNamespace(id="image-123")
    fake_client.containers.get.return_value = fake_container

    monkeypatch.setattr(RemoteDockerSandbox, "_get_docker_client", lambda: fake_client)
    monkeypatch.setattr(
        RemoteDockerSandbox,
        "_backup_remote_volume_to_tarball",
        lambda *, volume_name, backup_tar_path: backup_tar_path.write_bytes(
            b"shared-backup"
        ),
    )

    result = RemoteDockerSandbox.cache_sandboxes(
        {"main": SimpleNamespace(container_id="container-123")},
        "shared-vol",
        str(tmp_path),
        "task-1",
    )

    assert result["shared_volume_backup"]
    assert Path(result["shared_volume_backup"]).exists()
    manifest_path = tmp_path / "remote_docker_cache_manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["shared_volume_backup"] == result["shared_volume_backup"]
    assert "main" in manifest["sandboxes"]


def test_backup_remote_volume_to_tarball_extracts_archive(monkeypatch, tmp_path: Path):
    from opensage.sandbox.remote_docker_sandbox import RemoteDockerSandbox

    archive_bytes = _build_archive_bytes(
        "shared_volume.tar.gz", b"compressed-volume-content"
    )
    helper_container = mock.MagicMock()
    helper_container.exec_run.return_value = (0, b"")
    helper_container.get_archive.return_value = ([archive_bytes], {})

    fake_client = mock.MagicMock()
    fake_client.images.get.return_value = object()
    fake_client.containers.create.return_value = helper_container

    monkeypatch.setattr(RemoteDockerSandbox, "_get_docker_client", lambda: fake_client)

    backup_path = tmp_path / "backup.tar.gz"
    returned = RemoteDockerSandbox._backup_remote_volume_to_tarball(
        volume_name="shared-vol", backup_tar_path=backup_path
    )

    assert returned == str(backup_path)
    assert backup_path.read_bytes() == b"compressed-volume-content"


def test_opensandbox_cache_docker_runtime_delegates_to_remote_backend(
    monkeypatch, tmp_path: Path
):
    _set_backend_config(runtime_type="docker")
    monkeypatch.setattr(
        OpenSandboxSandbox,
        "_discover_remote_docker_container_id",
        classmethod(lambda cls, opensandbox_id: f"ctr-{opensandbox_id}"),
    )

    captured = {}

    def _fake_remote_cache(sandbox_instances, shared_volume_id, cache_dir, task_name):
        captured["sandbox_instances"] = sandbox_instances
        captured["shared_volume_id"] = shared_volume_id
        captured["cache_dir"] = cache_dir
        captured["task_name"] = task_name
        return {
            "backend": "remotedocker",
            "task_name": task_name,
            "cache_dir": cache_dir,
            "shared_volume_backup": str(tmp_path / "shared.tar.gz"),
            "cached_images": {
                "main": {
                    "image_name": "task_sandbox_main:cached",
                    "container_id": "ctr-sbx-main",
                }
            },
            "errors": [],
        }

    monkeypatch.setattr(
        "opensage.sandbox.opensandbox_sandbox.RemoteDockerSandbox.cache_sandboxes",
        _fake_remote_cache,
    )

    result = OpenSandboxSandbox.cache_sandboxes(
        {
            "main": SimpleNamespace(
                opensandbox_id="sbx-main",
                container_config_obj=SimpleNamespace(image="ubuntu:22.04"),
            )
        },
        "shared-vol",
        str(tmp_path),
        "task",
    )

    fake_instance = captured["sandbox_instances"]["main"]
    assert fake_instance.container_id == "ctr-sbx-main"
    assert captured["shared_volume_id"] == "shared-vol"
    assert result["cached_images"]["main"]["opensandbox_id"] == "sbx-main"
    assert result["runtime_type"] == "docker"
    assert (tmp_path / "opensandbox_cache_manifest.json").exists()


def test_opensandbox_cache_k8s_runtime_delegates_to_k8s_backend(
    monkeypatch, tmp_path: Path
):
    _set_backend_config(runtime_type="kubernetes")
    monkeypatch.setattr(
        OpenSandboxSandbox,
        "_discover_k8s_pod_and_container",
        classmethod(
            lambda cls, opensandbox_id, opensandbox_config, image_hint=None: (
                f"pod-{opensandbox_id}",
                "main-container",
            )
        ),
    )

    captured = {}

    def _fake_k8s_cache(sandbox_instances, shared_volume_id, cache_dir, task_name):
        captured["sandbox_instances"] = sandbox_instances
        return {
            "backend": "k8s",
            "task_name": task_name,
            "cache_dir": cache_dir,
            "shared_volume_backup": str(tmp_path / "shared.tar.gz"),
            "cached_images": {
                "main": {
                    "image_name": "task_sandbox_main:cached",
                    "pod_name": "pod-sbx-main",
                    "container_name": "main-container",
                    "commit_succeeded": False,
                    "rootfs_tar": str(tmp_path / "main-rootfs.tar.gz"),
                }
            },
            "errors": [],
        }

    monkeypatch.setattr(
        "opensage.sandbox.opensandbox_sandbox.K8sSandbox.cache_sandboxes",
        _fake_k8s_cache,
    )

    result = OpenSandboxSandbox.cache_sandboxes(
        {
            "main": SimpleNamespace(
                opensandbox_id="sbx-main",
                container_config_obj=SimpleNamespace(image="ubuntu:22.04"),
            )
        },
        "shared-pvc",
        str(tmp_path),
        "task-k8s",
    )

    fake_instance = captured["sandbox_instances"]["main"]
    assert fake_instance.pod_name == "pod-sbx-main"
    assert fake_instance.container_name == "main-container"
    assert result["cached_images"]["main"]["opensandbox_id"] == "sbx-main"
    assert result["runtime_type"] == "kubernetes"


def test_manager_initialize_shared_volumes_uses_opensandbox_backend(monkeypatch):
    config = _set_backend_config(runtime_type="docker")
    config.sandbox.sandboxes["worker"] = ContainerConfig(image="worker:latest")
    config.sandbox.mount_host_paths = [
        "/tmp/host-data:/workspace/host-data:ro",
        "/tmp/rw-data:/workspace/rw-data:rw",
    ]
    session = SimpleNamespace(opensage_session_id="session-1", config=config)
    manager = OpenSageSandboxManager(session)

    monkeypatch.setattr(
        "opensage.sandbox.shared_storage.SharedStorage.create_for_opensandbox",
        lambda session_id, init_data_path, tools_top_roots, config: (
            "scripts-vol",
            "shared-vol",
            "tools-vol",
        ),
    )

    manager.initialize_shared_volumes()

    assert manager._scripts_volume_id == "scripts-vol"
    assert manager._shared_volume_id == "shared-vol"
    assert manager._tools_volume_id == "tools-vol"
    assert "scripts-vol:/sandbox_scripts:ro" in config.sandbox.sandboxes["main"].volumes
    assert "shared-vol:/shared:rw" in config.sandbox.sandboxes["main"].volumes
    assert "tools-vol:/bash_tools:rw" in config.sandbox.sandboxes["main"].volumes
    assert (
        "/tmp/host-data:/workspace/host-data:ro"
        in config.sandbox.sandboxes["main"].volumes
    )
    assert (
        "/tmp/rw-data:/workspace/rw-data:rw" in config.sandbox.sandboxes["main"].volumes
    )
    assert (
        "scripts-vol:/sandbox_scripts:ro" in config.sandbox.sandboxes["worker"].volumes
    )
    assert (
        "/tmp/host-data:/workspace/host-data:ro"
        in config.sandbox.sandboxes["worker"].volumes
    )


def test_manager_mount_host_paths_validator():
    assert OpenSageSandboxManager._normalize_mount_host_path_spec("/a:/b") == "/a:/b:rw"
    assert (
        OpenSageSandboxManager._normalize_mount_host_path_spec("/a:/b:ro") == "/a:/b:ro"
    )
    with pytest.raises(ValueError, match="host path must be absolute"):
        OpenSageSandboxManager._normalize_mount_host_path_spec("rel:/b:rw")
    with pytest.raises(ValueError, match="container path must be absolute"):
        OpenSageSandboxManager._normalize_mount_host_path_spec("/a:rel:rw")
    with pytest.raises(ValueError, match="mode must be 'ro' or 'rw'"):
        OpenSageSandboxManager._normalize_mount_host_path_spec("/a:/b:rwx")


def test_manager_host_shared_mem_dir_mount_injected(monkeypatch, tmp_path: Path):
    config = _set_backend_config(runtime_type="docker")
    config.sandbox.sandboxes["worker"] = ContainerConfig(image="worker:latest")
    host_mem_dir = tmp_path / "shared_mem"
    config.sandbox.host_shared_mem_dir = str(host_mem_dir)
    session = SimpleNamespace(opensage_session_id="session-1", config=config)
    manager = OpenSageSandboxManager(session)

    monkeypatch.setattr(
        "opensage.sandbox.shared_storage.SharedStorage.create_for_opensandbox",
        lambda session_id, init_data_path, tools_top_roots, config: (
            "scripts-vol",
            "shared-vol",
            "tools-vol",
        ),
    )

    manager.initialize_shared_volumes()

    expected = f"{host_mem_dir}:/mem/shared:rw"
    assert expected in config.sandbox.sandboxes["main"].volumes
    assert expected in config.sandbox.sandboxes["worker"].volumes
    assert host_mem_dir.exists()
