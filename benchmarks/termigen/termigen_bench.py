"""TermiGen benchmark evaluation for OpenSage.

TermiGen (https://github.com/ucsb-mlsec/terminal-bench-env) ships thousands
of Harbor 2.0-formatted terminal tasks under ``environments_harbor/``. This
module clones that repository (if needed) and hands its tasks to
:class:`HarborEvaluation`, which already handles the Harbor task lifecycle
(Docker image build, agent run, ``tests/test.sh`` verification).

Usage::

    # Clone/refresh upstream and run all Harbor 2.0 tasks
    uv run python -m benchmarks.termigen.termigen_bench run

    # Run a single task
    uv run python -m benchmarks.termigen.termigen_bench run \\
        --task_file /path/to/task_ids.txt

    # Pin to a specific repo checkout
    uv run python -m benchmarks.termigen.termigen_bench run \\
        --repo_ref main --install_dir ~/.cache/termigen

    # Prepare the dataset without running anything
    uv run python -m benchmarks.termigen.termigen_bench prepare
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import fire

from benchmarks.harbor.harbor_evaluation import HarborEvaluation

logger = logging.getLogger(__name__)

DEFAULT_REPO_URL = "https://github.com/ucsb-mlsec/terminal-bench-env"
DEFAULT_INSTALL_DIR = Path.home() / ".cache" / "opensage" / "termigen"
HARBOR_TASKS_SUBDIR = "environments_harbor"


def _run_git(args: list[str]) -> None:
    """Run a git command, capturing output so normal progress doesn't leak
    into the benchmark's stderr. On failure raise RuntimeError with stderr."""
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed with exit {result.returncode}:\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


def _clone_or_update_repo(repo_url: str, repo_ref: str, install_dir: Path) -> Path:
    """Clone the termigen repo into ``install_dir``; update if already present.

    Returns the path to the cloned repository root.
    """
    install_dir = install_dir.expanduser().resolve()
    install_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = install_dir / "terminal-bench-env"

    if shutil.which("git") is None:
        raise FileNotFoundError(
            "git is required to fetch the termigen repository; install git and retry."
        )

    if not repo_dir.exists():
        logger.info("Cloning termigen repo %s into %s", repo_url, repo_dir)
        _run_git(
            ["clone", "--depth", "1", "--branch", repo_ref, repo_url, str(repo_dir)]
        )
    else:
        logger.info("Updating termigen repo at %s (ref=%s)", repo_dir, repo_ref)
        # Fetch the requested ref and snap the worktree to whatever was fetched.
        # Using FETCH_HEAD (rather than origin/<ref>) lets this work for
        # branches, tags, and commit SHAs uniformly.
        _run_git(["-C", str(repo_dir), "fetch", "--depth", "1", "origin", repo_ref])
        _run_git(["-C", str(repo_dir), "reset", "--hard", "FETCH_HEAD"])

    return repo_dir


def _resolve_tasks_dir(repo_dir: Path) -> Path:
    """Locate the Harbor 2.0 tasks directory inside the termigen repo."""
    tasks_dir = repo_dir / HARBOR_TASKS_SUBDIR
    if not tasks_dir.is_dir():
        raise FileNotFoundError(
            f"Expected Harbor tasks at {tasks_dir}; did the upstream layout change?"
        )
    has_task = any(
        (sub / "environment" / "Dockerfile").exists()
        for sub in tasks_dir.iterdir()
        if sub.is_dir()
    )
    if not has_task:
        raise RuntimeError(
            f"{tasks_dir} contains no Harbor tasks. The repo may be a shallow "
            "checkout missing LFS content — ensure `git lfs` is installed if needed."
        )
    return tasks_dir


@dataclass(kw_only=True)
class TermigenBench(HarborEvaluation):
    """Run TermiGen's Harbor 2.0 tasks through the existing Harbor evaluation.

    Subclasses :class:`HarborEvaluation`, so every Harbor knob
    (``agent_dir``, ``max_workers``, ``start_idx``/``end_idx``, ``task_file``,
    ``skip_existing``, ``test_timeout``...) is available here too.
    """

    name: str = "termigen"

    repo_url: str = DEFAULT_REPO_URL
    repo_ref: str = "main"
    install_dir: str = str(DEFAULT_INSTALL_DIR)
    """Parent directory that holds the cloned terminal-bench-env repo."""

    def __post_init__(self) -> None:
        if not self.dataset_path:
            repo_dir = _clone_or_update_repo(
                self.repo_url, self.repo_ref, Path(self.install_dir)
            )
            tasks_dir = _resolve_tasks_dir(repo_dir)
            self.dataset_path = str(tasks_dir)
            logger.info("Using TermiGen Harbor tasks from %s", tasks_dir)
        super().__post_init__()

    def prepare(self) -> str:
        """Clone/update the repo and print the resolved tasks directory."""
        repo_dir = _clone_or_update_repo(
            self.repo_url, self.repo_ref, Path(self.install_dir)
        )
        tasks_dir = _resolve_tasks_dir(repo_dir)
        print(tasks_dir)
        return str(tasks_dir)


if __name__ == "__main__":
    fire.Fire(TermigenBench)
