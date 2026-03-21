"""Unit tests for per-skill dependency installer runner."""

from __future__ import annotations

from typing import Any

import pytest

from opensage.config.config_dataclass import ContainerConfig
from opensage.sandbox.base_sandbox import BaseSandbox
from opensage.sandbox.skill_deps import prepare_skill_deps


class _FakeSandbox(BaseSandbox):
    """Minimal sandbox stub that can run prepare_skill_deps."""

    def __init__(self, *, enabled_skills: Any, sandbox_type: str = "main"):
        super().__init__(
            ContainerConfig(),
            opensage_session_id="sess-1",
            backend_type="native",
            sandbox_type=sandbox_type,
        )
        self._fake_enabled_skills = enabled_skills
        self._files: dict[str, str] = {}
        self._existing_files: set[str] = set()
        self.ran_installers: list[str] = []

    # --- BaseSandbox abstract methods (minimal stubs) ---
    def copy_file_from_container(self, src_path: str, dst_path: str):
        raise NotImplementedError()

    def copy_file_to_container(self, local_path: str, container_path: str):
        raise NotImplementedError()

    def extract_file_from_container(self, filepath: str) -> str:
        if filepath not in self._files:
            raise FileNotFoundError(filepath)
        return self._files[filepath]

    def extract_file_from_container_bytes(self, filepath: str) -> bytes:
        raise NotImplementedError()

    def run_command_in_container(
        self, command: str | list[str], timeout: int | None = None
    ) -> tuple[str, int]:
        del timeout
        cmd = command if isinstance(command, str) else " ".join(command)

        # test -f <path>
        if "test -f " in cmd:
            path = cmd.split("test -f ", 1)[1].strip().strip("'").strip('"')
            return ("", 0 if path in self._existing_files else 1)

        # mkdir -p ... and touch ... are treated as success
        if cmd.startswith("bash -lc mkdir -p "):
            return ("", 0)
        if cmd.startswith("bash -lc touch "):
            path = cmd.split("touch ", 1)[1].strip().strip("'").strip('"')
            self._existing_files.add(path)
            return ("", 0)

        # Installer execution: "chmod +x <installer> && <installer>"
        if "chmod +x " in cmd and "&&" in cmd:
            installer = cmd.split("chmod +x ", 1)[1].split("&&", 1)[0].strip()
            # Record installer path and succeed.
            self.ran_installers.append(installer)
            return ("ok", 0)

        # Find SKILL.md for folder expansion.
        if "find " in cmd and "-name SKILL.md" in cmd:
            # Very small simulation: return any SKILL.md files under /bash_tools.
            paths = []
            for path in sorted(self._files.keys()):
                if path.startswith("/bash_tools/") and path.endswith("/SKILL.md"):
                    paths.append(path)
            return ("\n".join(paths), 0 if paths else 1)

        # test -d <path> (treat /bash_tools/* as existing)
        if "test -d " in cmd:
            return ("", 0)

        return ("unexpected", 0)

    def get_work_dir(self):
        return "/"

    def delete_container(self) -> None:
        # Not needed for these unit tests.
        return None

    @classmethod
    def create_shared_volume(
        cls, volume_name_prefix, init_data_path=None, tools_top_roots=None
    ):
        raise NotImplementedError()

    @classmethod
    async def create_single_sandbox(
        cls, session_id: str, sandbox_type: str, container_config
    ):
        raise NotImplementedError()

    @classmethod
    async def launch_all_sandboxes(
        cls,
        session_id: str,
        sandbox_configs: dict,
        shared_volume_id: str = None,
        scripts_volume_id: str = None,
        tools_volume_id: str = None,
    ) -> dict:
        raise NotImplementedError()

    @classmethod
    def cache_sandboxes(
        cls,
        sandbox_instances: dict,
        shared_volume_id: str,
        cache_dir: str,
        task_name: str,
    ) -> dict:
        raise NotImplementedError()

    @classmethod
    def delete_shared_volumes(
        cls,
        scripts_volume_id: str = None,
        data_volume_id: str = None,
        tools_volume_id: str = None,
    ) -> None:
        raise NotImplementedError()

    @classmethod
    def checkpoint(cls) -> str:
        raise NotImplementedError()

    @classmethod
    def restore(cls) -> str:
        raise NotImplementedError()


@pytest.mark.asyncio
async def test_prepare_skill_deps_runs_matching_skill_installer():
    fake = _FakeSandbox(enabled_skills=["fuzz/my-skill"], sandbox_type="main")

    # Skill executes in "main" sandbox, has deps/install.sh
    fake._files["/bash_tools/fuzz/my-skill/SKILL.md"] = """---
name: my-skill
description: test
should_run_in_sandbox: main
returns_json: false
---
"""
    fake._existing_files.add("/bash_tools/fuzz/my-skill/deps/install.sh")

    await prepare_skill_deps(fake, enabled_skills=["fuzz/my-skill"])

    assert fake.ran_installers == ["/bash_tools/fuzz/my-skill/deps/install.sh"]


@pytest.mark.asyncio
async def test_prepare_skill_deps_skips_non_matching_sandbox():
    fake = _FakeSandbox(enabled_skills=["fuzz/my-skill"], sandbox_type="coverage")
    fake._files["/bash_tools/fuzz/my-skill/SKILL.md"] = """---
name: my-skill
description: test
should_run_in_sandbox: main
returns_json: false
---
"""
    fake._existing_files.add("/bash_tools/fuzz/my-skill/deps/install.sh")

    await prepare_skill_deps(fake, enabled_skills=["fuzz/my-skill"])
    assert fake.ran_installers == []


@pytest.mark.asyncio
async def test_prepare_skill_deps_skips_if_marker_exists():
    fake = _FakeSandbox(enabled_skills=["fuzz/my-skill"], sandbox_type="main")
    fake._files["/bash_tools/fuzz/my-skill/SKILL.md"] = """---
name: my-skill
description: test
should_run_in_sandbox: main
returns_json: false
---
"""
    fake._existing_files.add("/bash_tools/fuzz/my-skill/deps/install.sh")
    fake._existing_files.add("/shared/.opensage/skill_deps/main/fuzz_my-skill.done")

    await prepare_skill_deps(fake, enabled_skills=["fuzz/my-skill"])
    assert fake.ran_installers == []


@pytest.mark.asyncio
async def test_prepare_skill_deps_expands_top_level_folder():
    fake = _FakeSandbox(enabled_skills=["fuzz"], sandbox_type="main")

    fake._files["/bash_tools/fuzz/a/SKILL.md"] = """---
name: a
description: test
should_run_in_sandbox: main
returns_json: false
---
"""
    fake._existing_files.add("/bash_tools/fuzz/a/deps/install.sh")

    await prepare_skill_deps(fake, enabled_skills=["fuzz"])
    assert fake.ran_installers == ["/bash_tools/fuzz/a/deps/install.sh"]
