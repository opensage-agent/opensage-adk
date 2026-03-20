from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from opensage.config import (
    ContainerConfig,
    OpenSageConfig,
    OpenSandboxConfig,
    SandboxConfig,
)
from opensage.session.opensage_sandbox_manager import OpenSageSandboxManager


def _build_manager(
    *,
    backend: str,
    task_name: str = "cache-task",
    opensandbox_runtime_type: str = "docker",
) -> OpenSageSandboxManager:
    config = OpenSageConfig()
    config.task_name = task_name
    config.sandbox = SandboxConfig(
        backend=backend,
        sandboxes={
            "main": ContainerConfig(image="main:latest"),
            "worker": ContainerConfig(image="worker:latest"),
        },
    )
    if backend == "opensandbox":
        config.sandbox.opensandbox = OpenSandboxConfig(
            runtime_type=opensandbox_runtime_type
        )
    session = SimpleNamespace(opensage_session_id=f"{task_name}-session", config=config)
    return OpenSageSandboxManager(session)


def test_load_remote_docker_cache_manifest_updates_images_and_shared_backup(
    tmp_path: Path, monkeypatch
):
    manager = _build_manager(backend="remotedocker", task_name="remote-task")
    shared_backup = tmp_path / "remote-task_shared_volume.tar.gz"
    shared_backup.write_bytes(b"fake-shared-backup")

    manifest = {
        "task_name": "remote-task",
        "cache_dir": str(tmp_path),
        "shared_volume_backup": str(shared_backup),
        "sandboxes": {
            "main": {"image_name": "remote-task_sandbox_main:cached"},
            "worker": {"image_name": "remote-task_sandbox_worker:cached"},
        },
    }
    manifest_path = tmp_path / "remote_docker_cache_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setenv("OPENSAGE_REMOTE_DOCKER_CACHE_DIR", str(tmp_path))

    missing = manager.load_sandbox_caches_to_config()

    assert missing == []
    assert manager.config.sandbox.absolute_shared_data_path == str(shared_backup)
    assert (
        manager.config.sandbox.sandboxes["main"].image
        == "remote-task_sandbox_main:cached"
    )
    assert (
        manager.config.sandbox.sandboxes["worker"].image
        == "remote-task_sandbox_worker:cached"
    )
    assert manager.config.sandbox.sandboxes["main"].using_cached is True
    assert manager.config.sandbox.sandboxes["worker"].using_cached is True


def test_load_opensandbox_cache_manifest_updates_images_and_shared_backup(
    tmp_path: Path, monkeypatch
):
    manager = _build_manager(
        backend="opensandbox",
        task_name="opensandbox-task",
        opensandbox_runtime_type="docker",
    )
    shared_backup = tmp_path / "opensandbox-task_shared_volume.tar.gz"
    shared_backup.write_bytes(b"fake-shared-backup")

    manifest = {
        "task_name": "opensandbox-task",
        "cache_dir": str(tmp_path),
        "shared_volume_backup": str(shared_backup),
        "runtime_type": "docker",
        "sandboxes": {
            "main": {
                "image_name": "opensandbox-task_sandbox_main:cached",
                "opensandbox_id": "sbx-main",
            },
            "worker": {
                "image_name": "opensandbox-task_sandbox_worker:cached",
                "opensandbox_id": "sbx-worker",
            },
        },
    }
    manifest_path = tmp_path / "opensandbox_cache_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setenv("OPENSAGE_OPENSANDBOX_CACHE_DIR", str(tmp_path))

    missing = manager.load_sandbox_caches_to_config()

    assert missing == []
    assert manager.config.sandbox.absolute_shared_data_path == str(shared_backup)
    assert (
        manager.config.sandbox.sandboxes["main"].image
        == "opensandbox-task_sandbox_main:cached"
    )
    assert (
        manager.config.sandbox.sandboxes["worker"].image
        == "opensandbox-task_sandbox_worker:cached"
    )
    assert manager.config.sandbox.sandboxes["main"].using_cached is True
    assert manager.config.sandbox.sandboxes["worker"].using_cached is True


def test_load_opensandbox_k8s_cache_manifest_uses_rootfs_tar_when_commit_failed(
    tmp_path: Path, monkeypatch
):
    manager = _build_manager(
        backend="opensandbox",
        task_name="opensandbox-k8s-task",
        opensandbox_runtime_type="kubernetes",
    )
    shared_backup = tmp_path / "opensandbox-k8s-task_shared_volume.tar.gz"
    shared_backup.write_bytes(b"fake-shared-backup")
    rootfs_tar = tmp_path / "main_rootfs.tar.gz"
    rootfs_tar.write_bytes(b"fake-rootfs")

    manifest = {
        "task_name": "opensandbox-k8s-task",
        "cache_dir": str(tmp_path),
        "shared_volume_backup": str(shared_backup),
        "runtime_type": "kubernetes",
        "sandboxes": {
            "main": {
                "image_name": "opensandbox-k8s-task_sandbox_main:cached",
                "rootfs_tar": str(rootfs_tar),
                "commit_succeeded": False,
                "base_image": "ubuntu:22.04",
            }
        },
    }
    manifest_path = tmp_path / "opensandbox_cache_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setenv("OPENSAGE_OPENSANDBOX_CACHE_DIR", str(tmp_path))

    missing = manager.load_sandbox_caches_to_config()

    assert "worker" in missing
    assert "main" not in missing
    assert manager.config.sandbox.absolute_shared_data_path == str(shared_backup)
    assert manager.config.sandbox.sandboxes["main"].using_cached is True
    assert manager.config.sandbox.sandboxes["main"].extra["cached_rootfs_tar"] == str(
        rootfs_tar
    )
    assert (
        manager.config.sandbox.sandboxes["main"].extra["cached_base_image"]
        == "ubuntu:22.04"
    )
