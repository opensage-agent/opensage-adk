"""Unit tests for benchmarks/devopsgym/devops_gym.py helper functions.

These tests cover pure functions that don't require Docker, a running sandbox,
or the DevOps-Gym repository to be cloned.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers imported under test
# ---------------------------------------------------------------------------
from benchmarks.devopsgym.devops_gym import (
    _infer_category,
    _load_task_sample,
    _parse_docker_image_from_dockerfile,
    _parse_pytest_result,
)

# ===========================================================================
# _parse_docker_image_from_dockerfile
# ===========================================================================


class TestParseDockerImageFromDockerfile:
    def test_simple_from(self, tmp_path):
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM ubuntu:22.04\nRUN apt-get update\n")
        assert _parse_docker_image_from_dockerfile(dockerfile) == "ubuntu:22.04"

    def test_from_with_as(self, tmp_path):
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM python:3.11-slim AS builder\n")
        assert _parse_docker_image_from_dockerfile(dockerfile) == "python:3.11-slim"

    def test_from_uppercase(self, tmp_path):
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text(
            "FROM 3rdn4/devops-build-issues:apache-jackrabbit-47753810794\n"
        )
        result = _parse_docker_image_from_dockerfile(dockerfile)
        assert result == "3rdn4/devops-build-issues:apache-jackrabbit-47753810794"

    def test_comments_before_from(self, tmp_path):
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text(
            "# terminal-bench-canary GUID 8f3a2b1c\n"
            "FROM ghcr.io/laude-institute/t-bench/ubuntu-24-04:20250624\n"
            "RUN apt-get update\n"
        )
        assert (
            _parse_docker_image_from_dockerfile(dockerfile)
            == "ghcr.io/laude-institute/t-bench/ubuntu-24-04:20250624"
        )

    def test_missing_dockerfile_returns_none(self, tmp_path):
        assert _parse_docker_image_from_dockerfile(tmp_path / "nonexistent") is None

    def test_empty_dockerfile_returns_none(self, tmp_path):
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("# just a comment\n")
        assert _parse_docker_image_from_dockerfile(dockerfile) is None


# ===========================================================================
# _load_task_sample
# ===========================================================================


def _make_task_dir(
    tmp_path: Path, yaml_content: dict, with_dockerfile: bool = True
) -> Path:
    """Create a minimal task directory structure."""
    task_dir = tmp_path / "my_task"
    task_dir.mkdir()
    with open(task_dir / "task.yaml", "w") as f:
        yaml.dump(yaml_content, f)
    if with_dockerfile:
        (task_dir / "Dockerfile").write_text(
            "FROM 3rdn4/devops-build-issues:test-image\nRUN echo hello\n"
        )
    return task_dir


class TestLoadTaskSample:
    def test_basic_sample_with_docker_image_in_yaml(self, tmp_path):
        task_dir = _make_task_dir(
            tmp_path,
            {
                "instruction": "Fix the build failure.",
                "docker_image": "my-registry/my-image:tag",
                "difficulty": "medium",
                "max_agent_timeout_sec": 1200.0,
            },
        )
        sample = _load_task_sample(task_dir)
        assert sample is not None
        assert sample["task_id"] == "my_task"
        assert sample["instruction"] == "Fix the build failure."
        assert sample["docker_image"] == "my-registry/my-image:tag"
        assert sample["difficulty"] == "medium"
        assert sample["max_agent_timeout_sec"] == 1200.0
        assert sample["has_start_sh"] is False
        assert str(task_dir.resolve()) == sample["task_dir"]

    def test_docker_image_falls_back_to_dockerfile(self, tmp_path):
        task_dir = _make_task_dir(
            tmp_path,
            {"instruction": "Fix the build."},  # no docker_image in yaml
        )
        sample = _load_task_sample(task_dir)
        assert sample is not None
        assert sample["docker_image"] == "3rdn4/devops-build-issues:test-image"

    def test_has_start_sh_detected(self, tmp_path):
        task_dir = _make_task_dir(tmp_path, {"instruction": "Monitor the service."})
        (task_dir / "start.sh").write_text("#!/bin/bash\necho starting\n")
        sample = _load_task_sample(task_dir)
        assert sample is not None
        assert sample["has_start_sh"] is True

    def test_missing_task_yaml_returns_none(self, tmp_path):
        task_dir = tmp_path / "no_yaml_task"
        task_dir.mkdir()
        assert _load_task_sample(task_dir) is None

    def test_missing_instruction_returns_none(self, tmp_path):
        task_dir = _make_task_dir(
            tmp_path,
            {"docker_image": "some/image:tag"},  # no instruction
        )
        assert _load_task_sample(task_dir) is None

    def test_no_docker_image_anywhere_returns_none(self, tmp_path):
        task_dir = _make_task_dir(
            tmp_path,
            {"instruction": "Do something."},
            with_dockerfile=False,
        )
        assert _load_task_sample(task_dir) is None

    def test_multiline_instruction_preserved(self, tmp_path):
        instruction = textwrap.dedent("""\
            You are tasked with monitoring a server.

            Your task is to:
            1) Monitor system behaviour
            2) Write findings to /workspace/monitor_result.txt
        """)
        task_dir = _make_task_dir(
            tmp_path,
            {"instruction": instruction, "docker_image": "some/image:latest"},
        )
        sample = _load_task_sample(task_dir)
        assert sample is not None
        assert "monitor_result.txt" in sample["instruction"]

    def test_category_derived_from_parent_directory(self, tmp_path):
        cat_dir = tmp_path / "build"
        cat_dir.mkdir()
        task_dir = cat_dir / "build_bugfix__apache-jackrabbit-123"
        task_dir.mkdir()
        with open(task_dir / "task.yaml", "w") as f:
            yaml.dump({"instruction": "Fix it.", "docker_image": "img:tag"}, f)
        sample = _load_task_sample(task_dir)
        assert sample is not None
        assert sample["category"] == "build"


# ===========================================================================
# _parse_pytest_result
# ===========================================================================


class TestParsePytestResult:
    def test_clean_pass(self):
        stdout = (
            "🚀 Starting build test...\n"
            "\n"
            "===== EXIT CODE =====\n"
            "0\n"
            "\n"
            "===== short test summary info =====\n"
            "PASSED build_test.py::test_build_success\n"
        )
        assert _parse_pytest_result(stdout, exit_code=0) is True

    def test_failed_exit_code(self):
        stdout = "PASSED build_test.py::test_build_success\n"
        assert _parse_pytest_result(stdout, exit_code=1) is False

    def test_failed_line_in_stdout(self):
        stdout = (
            "===== short test summary info =====\n"
            "FAILED build_test.py::test_build_failure\n"
        )
        assert _parse_pytest_result(stdout, exit_code=0) is False

    def test_mixed_passed_and_failed_lines(self):
        stdout = "PASSED build_test.py::test_a\nFAILED build_test.py::test_b\n"
        assert _parse_pytest_result(stdout, exit_code=0) is False

    def test_empty_stdout_exit_zero(self):
        assert _parse_pytest_result("", exit_code=0) is True

    def test_empty_stdout_nonzero_exit(self):
        assert _parse_pytest_result("", exit_code=2) is False

    def test_failed_word_only_matches_whole_word(self):
        # "PREFAILED" or "FAILEDOVER" should not trigger a false positive
        stdout = "Task PREFAILED due to timeout\n"
        # \bFAILED\b won't match "PREFAILED"
        assert _parse_pytest_result(stdout, exit_code=0) is True

    def test_uppercase_failed_matches(self):
        stdout = "FAILED some_test.py::test_case\n"
        assert _parse_pytest_result(stdout, exit_code=0) is False


# ===========================================================================
# _infer_category
# ===========================================================================


class TestInferCategory:
    @pytest.mark.parametrize(
        "task_id, expected",
        [
            ("build_bugfix__apache-jackrabbit-47753810794", "build"),
            ("build_bugfix__elastic-logstash-49134052259", "build"),
            ("real_world_repos__grafana-high-cpu-usage", "monitor"),
            ("real_world_repos__prometheus-disk-leak", "monitor"),
            ("end_to_end_pipeline_1", "end_to_end"),
            ("alibaba__fastjson2-1245", "issue_resolving / test_generation"),
            (
                "spring-projects__spring-framework-33100",
                "issue_resolving / test_generation",
            ),
        ],
    )
    def test_known_patterns(self, task_id, expected):
        assert _infer_category(task_id) == expected

    def test_unknown_returns_unknown(self):
        assert _infer_category("something-completely-random") == "unknown"
