"""Unit tests for benchmarks/termigen/termigen_bench.py helper functions.

These tests cover pure helpers (``_resolve_tasks_dir`` and the module-level
constants) that do not require Docker, a running sandbox, or network access
to clone the upstream termigen repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.termigen.termigen_bench import (
    DEFAULT_INSTALL_DIR,
    DEFAULT_REPO_URL,
    HARBOR_TASKS_SUBDIR,
    _resolve_tasks_dir,
)


class TestModuleDefaults:
    def test_repo_url_points_to_upstream(self):
        assert DEFAULT_REPO_URL == "https://github.com/ucsb-mlsec/terminal-bench-env"

    def test_install_dir_under_user_cache(self):
        assert DEFAULT_INSTALL_DIR == Path.home() / ".cache" / "opensage" / "termigen"

    def test_harbor_tasks_subdir_name(self):
        assert HARBOR_TASKS_SUBDIR == "environments_harbor"


class TestResolveTasksDir:
    def test_missing_tasks_subdir_raises(self, tmp_path):
        # Repo without the expected Harbor 2.0 subdirectory.
        with pytest.raises(FileNotFoundError, match="Expected Harbor tasks"):
            _resolve_tasks_dir(tmp_path)

    def test_empty_tasks_subdir_raises(self, tmp_path):
        (tmp_path / HARBOR_TASKS_SUBDIR).mkdir()
        with pytest.raises(RuntimeError, match="contains no Harbor tasks"):
            _resolve_tasks_dir(tmp_path)

    def test_tasks_subdir_without_dockerfile_raises(self, tmp_path):
        # Subdirectories exist but none have environment/Dockerfile — looks like
        # a shallow checkout missing LFS content.
        tasks_dir = tmp_path / HARBOR_TASKS_SUBDIR
        tasks_dir.mkdir()
        (tasks_dir / "some_task").mkdir()
        (tasks_dir / "some_task" / "instruction.md").write_text("placeholder")
        with pytest.raises(RuntimeError, match="contains no Harbor tasks"):
            _resolve_tasks_dir(tmp_path)

    def test_valid_tasks_subdir_returns_path(self, tmp_path):
        tasks_dir = tmp_path / HARBOR_TASKS_SUBDIR
        task = tasks_dir / "hello_world_medium"
        (task / "environment").mkdir(parents=True)
        (task / "environment" / "Dockerfile").write_text("FROM alpine\n")
        (task / "instruction.md").write_text("Do it.")
        assert _resolve_tasks_dir(tmp_path) == tasks_dir

    def test_ignores_regular_files_at_top_level(self, tmp_path):
        # Stray files next to task directories must not trip the any() scan.
        tasks_dir = tmp_path / HARBOR_TASKS_SUBDIR
        tasks_dir.mkdir()
        (tasks_dir / "README.md").write_text("notes")
        task = tasks_dir / "t1"
        (task / "environment").mkdir(parents=True)
        (task / "environment" / "Dockerfile").write_text("FROM alpine\n")
        assert _resolve_tasks_dir(tmp_path) == tasks_dir
