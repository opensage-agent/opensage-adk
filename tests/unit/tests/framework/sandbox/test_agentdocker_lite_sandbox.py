"""Unit tests for AgentDockerLiteSandbox.

Requires: Linux root, overlayfs support in kernel.
btrfs tests additionally require a btrfs filesystem at /data.

Skip on CI: these need root privileges and btrfs.
Run on GCP VM:
    sudo ~/venv/bin/python -m pytest tests/unit/tests/framework/sandbox/test_agentdocker_lite_sandbox.py -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

from opensage.config.config_dataclass import ContainerConfig

needs_root = pytest.mark.skipif(
    os.geteuid() != 0, reason="AgentDockerLiteSandbox requires root"
)
needs_btrfs = pytest.mark.skipif(
    shutil.which("btrfs") is None
    or not Path("/data").exists()
    or subprocess.run(
        ["stat", "-f", "--format=%T", "/data"], capture_output=True, text=True
    ).stdout.strip()
    != "btrfs",
    reason="btrfs not available or /data is not btrfs",
)


def _collect_lib_deps(binary_path: str) -> set[str]:
    result = subprocess.run(["ldd", binary_path], capture_output=True, text=True)
    libs = set()
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if "=>" in line and len(parts) >= 3 and parts[2].startswith("/"):
            libs.add(parts[2])
        elif line.strip().startswith("/") and "ld-" in line:
            libs.add(parts[0])
    return libs


def _populate_rootfs(rootfs: Path):
    (rootfs / "bin").mkdir(exist_ok=True)
    (rootfs / "usr" / "bin").mkdir(parents=True, exist_ok=True)
    (rootfs / "tmp").mkdir(exist_ok=True)
    (rootfs / "etc").mkdir(exist_ok=True)
    (rootfs / "proc").mkdir(exist_ok=True)
    (rootfs / "dev").mkdir(exist_ok=True)
    (rootfs / "etc" / "os-release").write_text("NAME=test\nVERSION=1\n")

    all_libs: set[str] = set()

    for shell in ["bash", "sh"]:
        host = shutil.which(shell)
        if host:
            real = str(Path(os.path.realpath(host)))
            shutil.copy2(real, rootfs / "bin" / shell)
            all_libs |= _collect_lib_deps(real)

    for binary in [
        "ls",
        "cat",
        "echo",
        "touch",
        "test",
        "head",
        "mkdir",
        "cp",
        "sleep",
    ]:
        host = shutil.which(binary)
        if host:
            real = str(Path(os.path.realpath(host)))
            dest = rootfs / "usr" / "bin" / binary
            shutil.copy2(real, dest)
            alt = rootfs / "bin" / binary
            if not alt.exists():
                shutil.copy2(real, alt)
            all_libs |= _collect_lib_deps(real)

    for lib_path in all_libs:
        src = Path(lib_path).resolve()
        if not src.exists():
            continue
        dst = rootfs / lib_path.lstrip("/")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            try:
                shutil.copy2(str(src), str(dst))
            except (PermissionError, OSError):
                pass


def _make_overlayfs_rootfs(tmp: Path) -> Path:
    rootfs = tmp / "base_rootfs"
    rootfs.mkdir()
    _populate_rootfs(rootfs)
    return rootfs


def _make_btrfs_rootfs() -> str:
    safe_name = f"test_ns_{uuid.uuid4().hex[:8]}"
    base = Path("/data/rootfs_cache") / safe_name
    if base.exists():
        subprocess.run(["btrfs", "subvolume", "delete", str(base)], capture_output=True)
    subprocess.run(
        ["btrfs", "subvolume", "create", str(base)], capture_output=True, check=True
    )
    _populate_rootfs(base)
    return str(base)


# ------------------------------------------------------------------ #
#  overlayfs tests                                                     #
# ------------------------------------------------------------------ #


@needs_root
class TestAgentDockerLiteSandboxOverlayfs:
    def _make_sandbox(self, rootfs_path: str, session_suffix: str = ""):
        from opensage.sandbox.agentdocker_lite_sandbox import AgentDockerLiteSandbox

        cfg = ContainerConfig(
            image=rootfs_path,
            working_dir="/workspace",
            timeout=30,
            extra={"fs_backend": "overlayfs"},
        )
        sid = f"test_ovl_{uuid.uuid4().hex[:6]}{session_suffix}"
        return AgentDockerLiteSandbox(
            container_config=cfg,
            opensage_session_id=sid,
            backend_type="agentdocker-lite",
            sandbox_type="main",
        )

    def test_construct_and_delete(self, tmp_path):
        rootfs = _make_overlayfs_rootfs(tmp_path)
        sandbox = self._make_sandbox(str(rootfs))
        assert sandbox._inner._overlay_mounted
        assert sandbox._rootfs.exists()
        sandbox.delete_container()
        assert not sandbox._env_dir.exists()

    def test_run_command(self, tmp_path):
        rootfs = _make_overlayfs_rootfs(tmp_path)
        sandbox = self._make_sandbox(str(rootfs))
        try:
            out, rc = sandbox.run_command_in_container("echo hello_overlay")
            assert rc == 0
            assert "hello_overlay" in out
        finally:
            sandbox.delete_container()

    def test_working_dir_created(self, tmp_path):
        rootfs = _make_overlayfs_rootfs(tmp_path)
        sandbox = self._make_sandbox(str(rootfs))
        try:
            assert (sandbox._rootfs / "workspace").exists()
            out, rc = sandbox.run_command_in_container("pwd")
            assert rc == 0
            assert "/workspace" in out
        finally:
            sandbox.delete_container()

    def test_file_operations(self, tmp_path):
        rootfs = _make_overlayfs_rootfs(tmp_path)
        sandbox = self._make_sandbox(str(rootfs))
        try:
            sandbox.run_command_in_container(
                "echo test_content > /workspace/testfile.txt"
            )
            dst = str(tmp_path / "copied.txt")
            sandbox.copy_file_from_container("/workspace/testfile.txt", dst)
            assert Path(dst).read_text().strip() == "test_content"

            src_local = str(tmp_path / "upload.txt")
            Path(src_local).write_text("uploaded_content")
            sandbox.copy_file_to_container(src_local, "/workspace/upload.txt")
            out, rc = sandbox.run_command_in_container("cat /workspace/upload.txt")
            assert rc == 0
            assert "uploaded_content" in out
        finally:
            sandbox.delete_container()

    def test_copy_directory_from_container(self, tmp_path):
        rootfs = _make_overlayfs_rootfs(tmp_path)
        sandbox = self._make_sandbox(str(rootfs))
        try:
            sandbox.run_command_in_container(
                "mkdir -p /workspace/subdir && echo f1 > /workspace/file1.txt && echo f2 > /workspace/subdir/file2.txt"
            )
            dst = str(tmp_path / "copied_dir")
            sandbox.copy_directory_from_container("/workspace", dst)
            assert Path(dst).is_dir()
            assert (Path(dst) / "file1.txt").read_text().strip() == "f1"
            assert (Path(dst) / "subdir" / "file2.txt").read_text().strip() == "f2"
        finally:
            sandbox.delete_container()

    def test_reset_environment(self, tmp_path):
        rootfs = _make_overlayfs_rootfs(tmp_path)
        sandbox = self._make_sandbox(str(rootfs))
        try:
            sandbox.run_command_in_container("echo dirty > /tmp/marker")
            out, rc = sandbox.run_command_in_container("cat /tmp/marker")
            assert rc == 0
            assert "dirty" in out

            sandbox.reset_environment()
            out, rc = sandbox.run_command_in_container("cat /tmp/marker 2>&1")
            assert rc != 0 or "dirty" not in out
        finally:
            sandbox.delete_container()

    def test_get_work_dir(self, tmp_path):
        rootfs = _make_overlayfs_rootfs(tmp_path)
        sandbox = self._make_sandbox(str(rootfs))
        try:
            assert sandbox.get_work_dir() == "/workspace"
        finally:
            sandbox.delete_container()

    def test_command_timeout(self, tmp_path):
        rootfs = _make_overlayfs_rootfs(tmp_path)
        sandbox = self._make_sandbox(str(rootfs))
        try:
            out, rc = sandbox.run_command_in_container("sleep 10", timeout=1)
            assert rc == 124
            assert "timed out" in out.lower()
        finally:
            sandbox.delete_container()


# ------------------------------------------------------------------ #
#  btrfs tests                                                         #
# ------------------------------------------------------------------ #


@needs_root
@needs_btrfs
class TestAgentDockerLiteSandboxBtrfs:
    _base_rootfs: str | None = None

    @classmethod
    def setup_class(cls):
        cls._base_rootfs = _make_btrfs_rootfs()

    @classmethod
    def teardown_class(cls):
        if cls._base_rootfs and Path(cls._base_rootfs).exists():
            subprocess.run(
                ["btrfs", "subvolume", "delete", cls._base_rootfs],
                capture_output=True,
            )

    def _make_sandbox(self, session_suffix: str = ""):
        from opensage.sandbox.agentdocker_lite_sandbox import AgentDockerLiteSandbox

        cfg = ContainerConfig(
            image=self._base_rootfs,
            working_dir="/workspace",
            timeout=30,
            extra={
                "fs_backend": "btrfs",
                "env_base_dir": "/data/opensage_ns",
                "rootfs_cache_dir": "/data/rootfs_cache",
            },
        )
        sid = f"test_btrfs_{uuid.uuid4().hex[:6]}{session_suffix}"
        return AgentDockerLiteSandbox(
            container_config=cfg,
            opensage_session_id=sid,
            backend_type="agentdocker-lite",
            sandbox_type="main",
        )

    def test_construct_and_delete(self):
        sandbox = self._make_sandbox()
        assert sandbox._inner._btrfs_active
        assert sandbox._rootfs.exists()
        sandbox.delete_container()
        assert not sandbox._env_dir.exists()

    def test_run_command(self):
        sandbox = self._make_sandbox()
        try:
            out, rc = sandbox.run_command_in_container("echo hello_btrfs")
            assert rc == 0
            assert "hello_btrfs" in out
        finally:
            sandbox.delete_container()

    def test_working_dir_created(self):
        sandbox = self._make_sandbox()
        try:
            assert (sandbox._rootfs / "workspace").exists()
        finally:
            sandbox.delete_container()

    def test_reset_environment(self):
        sandbox = self._make_sandbox()
        try:
            sandbox.run_command_in_container("echo dirty > /tmp/marker")
            out, rc = sandbox.run_command_in_container("cat /tmp/marker")
            assert rc == 0 and "dirty" in out

            sandbox.reset_environment()
            out, rc = sandbox.run_command_in_container("cat /tmp/marker 2>&1")
            assert rc != 0 or "dirty" not in out
        finally:
            sandbox.delete_container()

    def test_copy_directory_from_container(self):
        sandbox = self._make_sandbox()
        try:
            sandbox.run_command_in_container(
                "mkdir -p /workspace/sub && echo a > /workspace/a.txt && echo b > /workspace/sub/b.txt"
            )
            with tempfile.TemporaryDirectory() as dst:
                out_dir = Path(dst) / "out"
                sandbox.copy_directory_from_container("/workspace", str(out_dir))
                assert (out_dir / "a.txt").read_text().strip() == "a"
                assert (out_dir / "sub" / "b.txt").read_text().strip() == "b"
        finally:
            sandbox.delete_container()

    def test_file_roundtrip(self):
        sandbox = self._make_sandbox()
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False
            ) as f:
                f.write("roundtrip_data")
                local_path = f.name

            sandbox.copy_file_to_container(local_path, "/workspace/rt.txt")
            os.unlink(local_path)

            out, rc = sandbox.run_command_in_container("cat /workspace/rt.txt")
            assert rc == 0 and "roundtrip_data" in out

            dst = local_path + ".back"
            sandbox.copy_file_from_container("/workspace/rt.txt", dst)
            assert Path(dst).read_text().strip() == "roundtrip_data"
            os.unlink(dst)
        finally:
            sandbox.delete_container()

    def test_multiple_resets(self):
        sandbox = self._make_sandbox()
        try:
            for i in range(3):
                sandbox.run_command_in_container(f"echo round_{i} > /tmp/marker")
                out, _ = sandbox.run_command_in_container("cat /tmp/marker")
                assert f"round_{i}" in out
                sandbox.reset_environment()
                out, rc = sandbox.run_command_in_container("cat /tmp/marker 2>&1")
                assert rc != 0 or f"round_{i}" not in out
        finally:
            sandbox.delete_container()


# ------------------------------------------------------------------ #
#  Error handling                                                      #
# ------------------------------------------------------------------ #


class TestAgentDockerLiteSandboxErrors:
    def test_non_root_uses_rootless(self):
        if os.geteuid() == 0:
            pytest.skip("Already root — cannot test rootless fallback")
        from opensage.sandbox.agentdocker_lite_sandbox import AgentDockerLiteSandbox

        # With agentdocker-lite, non-root auto-selects LandlockSandbox (rootless)
        # This should not raise PermissionError; it will fail for missing image instead
        cfg = ContainerConfig(image="/nonexistent", working_dir="/workspace")
        with pytest.raises((RuntimeError, FileNotFoundError, ValueError, OSError)):
            AgentDockerLiteSandbox(
                container_config=cfg,
                opensage_session_id="test",
                backend_type="agentdocker-lite",
                sandbox_type="main",
            )

    @needs_root
    def test_missing_image_raises(self):
        from opensage.sandbox.agentdocker_lite_sandbox import AgentDockerLiteSandbox

        cfg = ContainerConfig(
            image="/nonexistent_path_" + uuid.uuid4().hex[:8],
            working_dir="/workspace",
            extra={"fs_backend": "overlayfs"},
        )
        with pytest.raises((RuntimeError, FileNotFoundError, ValueError)):
            AgentDockerLiteSandbox(
                container_config=cfg,
                opensage_session_id="test_err",
                backend_type="agentdocker-lite",
                sandbox_type="main",
            )

    def test_wrong_backend_type_raises(self):
        from opensage.sandbox.agentdocker_lite_sandbox import AgentDockerLiteSandbox

        cfg = ContainerConfig(image="/tmp", working_dir="/workspace")
        with pytest.raises(
            AssertionError, match="requires backend_type='agentdocker-lite'"
        ):
            AgentDockerLiteSandbox(
                container_config=cfg,
                opensage_session_id="test",
                backend_type="native",
                sandbox_type="main",
            )


if __name__ == "__main__":
    import sys

    pytest.main([__file__, "-v"] + sys.argv[1:])
