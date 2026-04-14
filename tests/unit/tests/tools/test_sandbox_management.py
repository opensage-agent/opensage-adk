"""Tests for sandbox management tools (create, exec, stop, list)."""

from __future__ import annotations

from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from opensage.toolbox.general.sandbox_management import (
    _truncate,
    create_sandbox,
    exec_in_sandbox,
    list_active_sandboxes,
    stop_sandbox,
)

# ---------------------------------------------------------------------------
# Helpers / Fakes
# ---------------------------------------------------------------------------


class FakeSandbox:
    """Minimal sandbox stand-in for unit tests.

    Mirrors BaseSandbox by exposing the config on ``container_config_obj``
    (the real attribute name). The plain ``container_config`` alias is
    kept for tests that exercise the fallback code path.
    """

    def __init__(
        self,
        *,
        image: str = "ubuntu:24.04",
        working_dir: str = "/workspace",
        state_value: str = "READY",
    ):
        self.state = MagicMock(value=state_value)
        config = MagicMock(image=image, working_dir=working_dir)
        self.container_config_obj = config
        self.container_config = config
        self._run_results: list[tuple[str, int]] = [("", 0)]

    def set_run_result(self, output: str, exit_code: int = 0) -> None:
        self._run_results = [(output, exit_code)]

    def run_command_in_container(self, command, timeout=None):
        return self._run_results[0]


class FakeManager:
    """Minimal OpenSageSandboxManager stand-in."""

    def __init__(self, sandboxes: Optional[Dict[str, FakeSandbox]] = None):
        self._sandboxes: Dict[str, FakeSandbox] = sandboxes or {}
        self.opensage_session_id = "test-session-001"
        self._scripts_volume_id: Optional[str] = None
        self._tools_volume_id: Optional[str] = None
        self._shared_volume_id: Optional[str] = None
        self.enabled_skills: Any = None

    def list_sandboxes(self):
        return dict(self._sandboxes)

    def get_sandbox(self, sandbox_type):
        return self._sandboxes[sandbox_type]

    def get_shared_volume(self):
        return self._shared_volume_id

    def remove_sandbox(self, sandbox_type):
        if sandbox_type in self._sandboxes:
            del self._sandboxes[sandbox_type]
            return True
        return False


class FakeSession:
    def __init__(self, manager: FakeManager):
        self.sandboxes = manager
        self.config = MagicMock()
        self.config.sandbox = MagicMock()
        self.config.sandbox.backend = "native"
        self.config.sandbox.add_or_update_sandbox = MagicMock()


def _make_tool_context():
    return MagicMock()


def _patch_session(session):
    return patch(
        "opensage.toolbox.general.sandbox_management.get_opensage_session_from_context",
        return_value=session,
    )


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_short_text_unchanged(self):
        assert _truncate("hello") == "hello"

    def test_long_text_truncated(self):
        text = "x" * 100_000
        result = _truncate(text)
        assert len(result) < len(text)
        assert "truncated" in result
        assert "100000 bytes total" in result

    def test_exact_boundary(self):
        text = "a" * (50 * 1024)
        assert _truncate(text) == text  # exactly at limit, no truncation


# ---------------------------------------------------------------------------
# list_active_sandboxes
# ---------------------------------------------------------------------------


class TestListActiveSandboxes:
    def test_empty(self):
        mgr = FakeManager()
        session = FakeSession(mgr)
        ctx = _make_tool_context()
        with _patch_session(session):
            result = list_active_sandboxes(ctx)
        assert result["count"] == 0
        assert result["sandboxes"] == {}

    def test_with_sandboxes(self):
        mgr = FakeManager(
            {
                "main": FakeSandbox(image="ubuntu:22.04"),
                "target": FakeSandbox(image="nginx:latest", working_dir="/app"),
            }
        )
        session = FakeSession(mgr)
        ctx = _make_tool_context()
        with _patch_session(session):
            result = list_active_sandboxes(ctx)
        assert result["count"] == 2
        assert "main" in result["sandboxes"]
        assert "target" in result["sandboxes"]
        assert result["sandboxes"]["main"]["image"] == "ubuntu:22.04"
        assert result["sandboxes"]["target"]["working_dir"] == "/app"

    def test_reads_container_config_obj(self):
        """list_active_sandboxes() must read the real attribute name."""
        sb = MagicMock()
        sb.state = MagicMock(value="READY")
        sb.container_config_obj = MagicMock(image="real:img", working_dir="/real")
        # Ensure the fallback attribute is not present
        del sb.container_config

        mgr = FakeManager({"x": sb})
        session = FakeSession(mgr)
        ctx = _make_tool_context()
        with _patch_session(session):
            result = list_active_sandboxes(ctx)

        assert result["sandboxes"]["x"]["image"] == "real:img"
        assert result["sandboxes"]["x"]["working_dir"] == "/real"


# ---------------------------------------------------------------------------
# exec_in_sandbox
# ---------------------------------------------------------------------------


class TestExecInSandbox:
    @pytest.mark.asyncio
    async def test_success(self):
        sb = FakeSandbox()
        sb.set_run_result("hello world\n", 0)
        mgr = FakeManager({"main": sb})
        session = FakeSession(mgr)
        ctx = _make_tool_context()
        with _patch_session(session):
            result = await exec_in_sandbox("main", "echo hello world", ctx)
        assert "hello world" in result

    @pytest.mark.asyncio
    async def test_nonzero_exit(self):
        sb = FakeSandbox()
        sb.set_run_result("not found", 127)
        mgr = FakeManager({"main": sb})
        session = FakeSession(mgr)
        ctx = _make_tool_context()
        with _patch_session(session):
            result = await exec_in_sandbox("main", "bad_cmd", ctx)
        assert "[exit_code: 127]" in result

    @pytest.mark.asyncio
    async def test_sandbox_not_found(self):
        mgr = FakeManager({"main": FakeSandbox()})
        session = FakeSession(mgr)
        ctx = _make_tool_context()
        with _patch_session(session):
            result = await exec_in_sandbox("nonexistent", "echo hi", ctx)
        assert "not found" in result
        assert "main" in result  # lists active sandboxes

    @pytest.mark.asyncio
    async def test_empty_output(self):
        sb = FakeSandbox()
        sb.set_run_result("", 0)
        mgr = FakeManager({"main": sb})
        session = FakeSession(mgr)
        ctx = _make_tool_context()
        with _patch_session(session):
            result = await exec_in_sandbox("main", "true", ctx)
        assert result == "(no output)"

    @pytest.mark.asyncio
    async def test_output_truncation(self):
        sb = FakeSandbox()
        sb.set_run_result("x" * 100_000, 0)
        mgr = FakeManager({"main": sb})
        session = FakeSession(mgr)
        ctx = _make_tool_context()
        with _patch_session(session):
            result = await exec_in_sandbox("main", "generate_lots", ctx)
        assert "truncated" in result


# ---------------------------------------------------------------------------
# stop_sandbox
# ---------------------------------------------------------------------------


class TestStopSandbox:
    def test_stop_existing(self):
        mgr = FakeManager({"main": FakeSandbox(), "target": FakeSandbox()})
        session = FakeSession(mgr)
        ctx = _make_tool_context()
        with _patch_session(session):
            result = stop_sandbox("target", ctx)
        assert result["success"] is True
        assert result["stopped"] == "target"
        assert "target" not in result["active_sandboxes"]
        assert "main" in result["active_sandboxes"]

    def test_stop_main_blocked(self):
        mgr = FakeManager({"main": FakeSandbox()})
        session = FakeSession(mgr)
        ctx = _make_tool_context()
        with _patch_session(session):
            result = stop_sandbox("main", ctx)
        assert result["success"] is False
        assert "Cannot stop" in result["error"]

    def test_stop_nonexistent(self):
        mgr = FakeManager({"main": FakeSandbox()})
        session = FakeSession(mgr)
        ctx = _make_tool_context()
        with _patch_session(session):
            result = stop_sandbox("ghost", ctx)
        assert result["success"] is False
        assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# create_sandbox
# ---------------------------------------------------------------------------


class TestCreateSandbox:
    @pytest.mark.asyncio
    async def test_duplicate_name_rejected(self):
        mgr = FakeManager({"main": FakeSandbox()})
        session = FakeSession(mgr)
        ctx = _make_tool_context()
        with _patch_session(session):
            result = await create_sandbox("main", "ubuntu:24.04", ctx)
        assert result["success"] is False
        assert "already exists" in result["error"]

    @pytest.mark.asyncio
    async def test_create_success(self):
        """Test successful sandbox creation with mocked backend."""
        mgr = FakeManager({"main": FakeSandbox()})
        session = FakeSession(mgr)
        ctx = _make_tool_context()

        new_sb = FakeSandbox(image="nginx:latest", working_dir="/app")
        mock_backend = MagicMock()
        mock_backend.launch_all_sandboxes = AsyncMock(return_value={"target": new_sb})
        mock_backend.initialize_all_sandboxes = AsyncMock(return_value={})

        with (
            _patch_session(session),
            patch(
                "opensage.sandbox.factory.get_backend_class",
                return_value=mock_backend,
            ),
        ):
            result = await create_sandbox(
                "target",
                "nginx:latest",
                ctx,
                working_dir="/app",
                network=True,
            )

        assert result["success"] is True
        assert result["sandbox_type"] == "target"
        assert result["image"] == "nginx:latest"
        assert result["network"] is True
        assert "target" in result["active_sandboxes"]
        # Verify the sandbox was registered in the manager
        assert "target" in mgr._sandboxes
        # initialize_all_sandboxes must receive the FULL sandbox map so peer
        # initializers (e.g. asserting 'main' is present) keep working.
        init_call_args = mock_backend.initialize_all_sandboxes.call_args
        init_map = init_call_args[0][0]
        assert "main" in init_map
        assert "target" in init_map

    @pytest.mark.asyncio
    async def test_create_injects_shared_volume_mounts(self):
        """Dynamic sandbox must inherit the session's shared volume mounts.

        The native Docker backend ignores shared/scripts/tools_volume_id
        kwargs and reads mounts from container_config.volumes — so the
        tool must inject them itself.
        """
        mgr = FakeManager({"main": FakeSandbox()})
        mgr._scripts_volume_id = "scripts-vol-abc"
        mgr._shared_volume_id = "shared-vol-abc"
        mgr._tools_volume_id = "tools-vol-abc"
        session = FakeSession(mgr)
        ctx = _make_tool_context()

        new_sb = FakeSandbox()
        mock_backend = MagicMock()
        mock_backend.launch_all_sandboxes = AsyncMock(return_value={"target": new_sb})
        mock_backend.initialize_all_sandboxes = AsyncMock(return_value={})

        with (
            _patch_session(session),
            patch(
                "opensage.sandbox.factory.get_backend_class",
                return_value=mock_backend,
            ),
        ):
            await create_sandbox("target", "ubuntu:24.04", ctx)

        # Pull the ContainerConfig that was passed to launch_all_sandboxes
        launch_call = mock_backend.launch_all_sandboxes.call_args
        configs = launch_call[1]["sandbox_configs"]
        cfg = configs["target"]
        assert "scripts-vol-abc:/sandbox_scripts:ro" in cfg.volumes
        assert "shared-vol-abc:/shared:rw" in cfg.volumes
        assert "tools-vol-abc:/bash_tools:rw" in cfg.volumes

    @pytest.mark.asyncio
    async def test_create_runs_skill_deps(self):
        """When enabled_skills is set, prepare_skill_deps must run."""
        mgr = FakeManager({"main": FakeSandbox()})
        mgr.enabled_skills = "all"
        session = FakeSession(mgr)
        ctx = _make_tool_context()

        new_sb = FakeSandbox()
        mock_backend = MagicMock()
        mock_backend.launch_all_sandboxes = AsyncMock(return_value={"target": new_sb})
        mock_backend.initialize_all_sandboxes = AsyncMock(return_value={})

        prepare_mock = AsyncMock()
        with (
            _patch_session(session),
            patch(
                "opensage.sandbox.factory.get_backend_class",
                return_value=mock_backend,
            ),
            patch(
                "opensage.sandbox.skill_deps.prepare_skill_deps",
                prepare_mock,
            ),
        ):
            await create_sandbox("target", "ubuntu:24.04", ctx)

        prepare_mock.assert_awaited_once()
        # First positional arg is the new sandbox instance
        await_args = prepare_mock.await_args
        assert await_args is not None
        args = await_args[0]
        assert args[0] is new_sb
        assert args[1] == "all"

    @pytest.mark.asyncio
    async def test_create_backend_failure(self):
        """Test handling when backend fails to create the sandbox."""
        mgr = FakeManager({"main": FakeSandbox()})
        session = FakeSession(mgr)
        ctx = _make_tool_context()

        mock_backend = MagicMock()
        mock_backend.launch_all_sandboxes = AsyncMock(return_value={})
        mock_backend.initialize_all_sandboxes = AsyncMock(return_value={})

        with (
            _patch_session(session),
            patch(
                "opensage.sandbox.factory.get_backend_class",
                return_value=mock_backend,
            ),
        ):
            result = await create_sandbox("target", "bad:image", ctx)

        assert result["success"] is False
        assert "Backend failed" in result["error"]

    @pytest.mark.asyncio
    async def test_create_with_volumes_and_env(self):
        """Test that volume and environment parsing works."""
        mgr = FakeManager({"main": FakeSandbox()})
        session = FakeSession(mgr)
        ctx = _make_tool_context()

        new_sb = FakeSandbox()
        mock_backend = MagicMock()
        mock_backend.launch_all_sandboxes = AsyncMock(return_value={"db": new_sb})
        mock_backend.initialize_all_sandboxes = AsyncMock(return_value={})

        with (
            _patch_session(session),
            patch(
                "opensage.sandbox.factory.get_backend_class",
                return_value=mock_backend,
            ),
        ):
            result = await create_sandbox(
                "db",
                "postgres:16",
                ctx,
                volumes="/data:/var/lib/postgresql:rw,/conf:/etc/conf:ro",
                environment="POSTGRES_PASSWORD=secret,PGDATA=/data",
            )

        assert result["success"] is True
        # Check that add_or_update_sandbox was called with correct config
        call_args = session.config.sandbox.add_or_update_sandbox.call_args
        config_arg = call_args[0][1]  # second positional arg
        assert len(config_arg.volumes) == 2
        assert "/data:/var/lib/postgresql:rw" in config_arg.volumes
        assert config_arg.environment["POSTGRES_PASSWORD"] == "secret"
        assert config_arg.environment["PGDATA"] == "/data"

    @pytest.mark.asyncio
    async def test_create_exception_handled(self):
        """Test that exceptions during creation are caught gracefully."""
        mgr = FakeManager({})
        session = FakeSession(mgr)
        ctx = _make_tool_context()

        mock_backend = MagicMock()
        mock_backend.launch_all_sandboxes = AsyncMock(
            side_effect=RuntimeError("Docker daemon not reachable")
        )

        with (
            _patch_session(session),
            patch(
                "opensage.sandbox.factory.get_backend_class",
                return_value=mock_backend,
            ),
        ):
            result = await create_sandbox("target", "ubuntu:24.04", ctx)

        assert result["success"] is False
        assert "Docker daemon" in result["error"]
