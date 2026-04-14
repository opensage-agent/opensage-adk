"""DevOps-Gym benchmark integration for OpenSage.

DevOps-Gym is a benchmark evaluating AI agents across the full DevOps cycle:
build & configuration, monitoring, issue resolution, test generation, and
end-to-end pipelines.

Prerequisites:
    Clone the DevOps-Gym repository before running:
        git clone https://github.com/ucsb-mlsec/DevOps-Gym third_party/devops-gym

Usage:
    python benchmarks/devopsgym/devops_gym.py run \\
        --task_category build \\
        --max_workers 4 \\
        --start_idx 0 \\
        --end_idx 5

    python benchmarks/devopsgym/devops_gym.py run_debug \\
        --task_category issue_resolving

Reference: https://github.com/ucsb-mlsec/DevOps-Gym
"""

import asyncio
import datetime
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import datasets
import fire
import yaml
from google.adk.sessions import Session

from opensage.evaluation.base import Evaluation, EvaluationTask
from opensage.utils.project_info import PROJECT_PATH

logger = logging.getLogger(__name__)

VALID_CATEGORIES = (
    "build",
    "monitor",
    "issue_resolving",
    "test_generation",
    "end_to_end",
)

# Files that must NOT be visible to the agent during execution to avoid leaking answers.
# They are injected into the container after the agent session ends (see _collect_outputs).
_SENSITIVE_FILES = {"run-tests.sh", "solution.sh"}


def _parse_docker_image_from_dockerfile(dockerfile_path: Path) -> str | None:
    """Extract the base image from the first FROM line of a Dockerfile."""
    try:
        with open(dockerfile_path) as f:
            for line in f:
                line = line.strip()
                if line.upper().startswith("FROM "):
                    parts = line.split()
                    # FROM <image> [AS <name>]
                    if len(parts) >= 2:
                        return parts[1]
    except OSError:
        pass
    return None


def _load_task_sample(task_dir: Path) -> dict | None:
    """Load a single DevOps-Gym task directory into a sample dict.

    Returns None if the directory is missing required files.
    """
    task_yaml_path = task_dir / "task.yaml"
    if not task_yaml_path.exists():
        logger.warning("Skipping %s: task.yaml not found", task_dir)
        return None

    with open(task_yaml_path) as f:
        task_meta = yaml.safe_load(f) or {}

    instruction = task_meta.get("instruction", "")
    if not instruction:
        logger.warning("Skipping %s: no instruction in task.yaml", task_dir)
        return None

    # Resolve docker image: task.yaml field takes priority; fall back to Dockerfile FROM
    docker_image: str | None = task_meta.get("docker_image")
    if not docker_image:
        dockerfile_path = task_dir / "Dockerfile"
        docker_image = _parse_docker_image_from_dockerfile(dockerfile_path)
    if not docker_image:
        logger.warning("Skipping %s: cannot determine docker image", task_dir)
        return None

    # Derive category from the grandparent directory name (tasks/<category>/<task_id>)
    category = task_dir.parent.name

    return {
        "task_id": task_dir.name,
        "task_dir": str(task_dir.resolve()),
        "category": category,
        "instruction": instruction,
        "docker_image": docker_image,
        "has_start_sh": (task_dir / "start.sh").exists(),
        # Optional timing hints from task.yaml
        "max_agent_timeout_sec": task_meta.get("max_agent_timeout_sec"),
        "max_test_timeout_sec": task_meta.get("max_test_timeout_sec"),
        "difficulty": task_meta.get("difficulty", ""),
    }


@dataclass(kw_only=True)
class DevOpsGym(Evaluation):
    """Evaluation class for the DevOps-Gym benchmark.

    DevOps-Gym tests AI agents on real-world DevOps tasks across five categories:
    - build: Fix build failures and dependency issues (48 tasks)
    - monitor: Diagnose runtime anomalies via observation (30 tasks)
    - issue_resolving: Generate patches for GitHub issues (310 tasks)
    - test_generation: Write tests that verify issue fixes (310 tasks)
    - end_to_end: Multi-stage DevOps pipelines (14 tasks)

    The sandbox uses the task's own Docker image (from task.yaml or Dockerfile).
    Evaluation is performed by injecting run-tests.sh into the live container
    AFTER the agent session ends, so the script is never visible to the agent.

    Prerequisites:
        git clone https://github.com/ucsb-mlsec/DevOps-Gym third_party/devops-gym
    """

    # Required fields from Evaluation base (provide defaults here)
    dataset_path: str = ""
    agent_dir: str = str(PROJECT_PATH / "examples" / "agents" / "devops_gym_agent")
    config_template_path: str = str(
        PROJECT_PATH / "examples" / "agents" / "devops_gym_agent" / "config.toml"
    )

    name: str = "devopsgym"
    run_until_explicit_finish: bool = False
    max_llm_calls: int = 150
    max_workers: int = 4

    # DevOps-Gym specific
    devops_gym_root: str = str(PROJECT_PATH / "third_party" / "devops-gym")
    """Path to the cloned DevOps-Gym repository."""

    task_category: str | None = None
    """One of: build, monitor, issue_resolving, test_generation, end_to_end.
    If None, all categories are loaded."""

    # Dataset slice / filtering
    start_idx: int = 0
    end_idx: int | None = None
    task_file: str | None = None
    """Path to a file listing task_ids to run (one per line)."""
    skip_existing: bool = False
    """Skip tasks that already have an output folder."""

    # Grading timeout (seconds): how long run-tests.sh is allowed to run
    grade_timeout_sec: int = 300

    def __post_init__(self):
        super().__post_init__()
        gym_root = Path(self.devops_gym_root)
        if not gym_root.exists():
            raise FileNotFoundError(
                f"DevOps-Gym root not found at '{gym_root}'. "
                "Please clone it first:\n"
                "  git clone https://github.com/ucsb-mlsec/DevOps-Gym "
                f"{gym_root}"
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Dataset loading
    # ──────────────────────────────────────────────────────────────────────────

    def _get_dataset(self) -> datasets.Dataset:
        gym_root = Path(self.devops_gym_root)
        tasks_root = gym_root / "tasks"

        if self.task_category is not None:
            cats = [self.task_category]
            for cat in cats:
                if cat not in VALID_CATEGORIES:
                    raise ValueError(
                        f"Invalid task_category '{cat}'. "
                        f"Valid values: {VALID_CATEGORIES}"
                    )
        else:
            cats = [c for c in VALID_CATEGORIES if (tasks_root / c).is_dir()]

        samples = []
        for cat in cats:
            cat_dir = tasks_root / cat
            if not cat_dir.is_dir():
                logger.warning("Category directory not found, skipping: %s", cat_dir)
                continue
            for task_dir in sorted(cat_dir.iterdir()):
                if not task_dir.is_dir():
                    continue
                sample = _load_task_sample(task_dir)
                if sample is not None:
                    samples.append(sample)

        if not samples:
            raise RuntimeError(
                f"No valid tasks found under '{tasks_root}' "
                f"(categories: {cats}). "
                "Make sure the DevOps-Gym repository is fully cloned."
            )

        logger.info("Loaded %d DevOps-Gym tasks (categories: %s)", len(samples), cats)
        dataset = datasets.Dataset.from_list(samples)

        # ── optional task_file filter ──────────────────────────────────────
        if self.task_file:
            task_file_path = Path(self.task_file)
            if task_file_path.exists():
                with open(task_file_path) as f:
                    wanted_ids = {ln.strip() for ln in f if ln.strip()}
                indices = [
                    i for i, s in enumerate(dataset) if s["task_id"] in wanted_ids
                ]
                dataset = dataset.select(indices) if indices else dataset.select([])
                logger.info(
                    "task_file filter: %d tasks match %s", len(dataset), self.task_file
                )
            else:
                logger.warning("task_file not found: %s", self.task_file)

        # ── index slice ───────────────────────────────────────────────────
        if self.end_idx is not None:
            end = min(self.end_idx, len(dataset))
            start = min(self.start_idx, end)
            dataset = dataset.select(range(start, end))
        elif self.start_idx > 0:
            dataset = dataset.select(
                range(min(self.start_idx, len(dataset)), len(dataset))
            )

        # ── skip_existing ─────────────────────────────────────────────────
        if self.skip_existing and self.output_dir and Path(self.output_dir).exists():
            existing_ids = {
                d.name
                for d in Path(self.output_dir).iterdir()
                if d.is_dir() and d.name not in ("results", "__pycache__")
            }
            if existing_ids:
                pre = len(dataset)
                indices = [
                    i for i, s in enumerate(dataset) if s["task_id"] not in existing_ids
                ]
                dataset = dataset.select(indices) if indices else dataset.select([])
                logger.info(
                    "skip_existing: skipped %d tasks, %d remaining",
                    pre - len(dataset),
                    len(dataset),
                )

        logger.info("Final dataset size: %d tasks", len(dataset))
        return dataset

    # ──────────────────────────────────────────────────────────────────────────
    # Task construction
    # ──────────────────────────────────────────────────────────────────────────

    def _get_task_id(self, sample: dict) -> str:
        return sample["task_id"]

    def _get_first_user_message(self, sample: dict) -> str:
        return sample["instruction"]

    def _get_export_dir_in_sandbox(self, sample: dict) -> str | None:
        # Export /workspace so output files (e.g. monitor_result.txt) are preserved
        return "/workspace"

    def _get_initial_data_dir(self, sample: dict) -> str | None:
        # We do NOT pass the task directory as shared data; run-tests.sh must
        # not be visible to the agent.  The container already has everything it
        # needs baked into the Docker image.
        return None

    # ──────────────────────────────────────────────────────────────────────────
    # Config template
    # ──────────────────────────────────────────────────────────────────────────

    def _get_config_template_variables(self, task: EvaluationTask) -> dict:
        tmpl = super()._get_config_template_variables(task)
        # Docker image names must be lowercase
        tmpl["TASK_NAME"] = task.id.lower()
        tmpl["DEFAULT_IMAGE"] = task.sample["docker_image"].lower()
        return tmpl

    # ──────────────────────────────────────────────────────────────────────────
    # Sandbox lifecycle hooks
    # ──────────────────────────────────────────────────────────────────────────

    async def _after_initialize_callback(self, task: EvaluationTask) -> None:
        """For monitor-category tasks: run start.sh in the background inside
        the container so the environment is active before the agent starts."""
        sample = task.sample
        if not sample.get("has_start_sh"):
            return

        task_dir = Path(sample["task_dir"])
        start_sh = task_dir / "start.sh"
        if not start_sh.exists():
            return

        sandbox = task.opensage_session.sandboxes.get_sandbox("main")

        # Copy start.sh into the container at /workspace/start.sh
        try:
            sandbox.copy_file_to_container(str(start_sh), "/workspace/start.sh")
            logger.info("Copied start.sh into container for task %s", task.id)
        except Exception as e:
            logger.warning("Failed to copy start.sh for task %s: %s", task.id, e)
            return

        # Run it in the background; give it 10 s to boot services before continuing
        cmd = "chmod +x /workspace/start.sh && nohup bash /workspace/start.sh > /workspace/start_sh.log 2>&1 &"
        output, exit_code = sandbox.run_command_in_container(cmd)
        logger.info("start.sh launched for task %s (exit_code=%s)", task.id, exit_code)

        # Brief wait to let the service come up
        await asyncio.sleep(10)

    # ──────────────────────────────────────────────────────────────────────────
    # Output collection + grading (called while container is still alive)
    # ──────────────────────────────────────────────────────────────────────────

    async def _collect_outputs(self, task: EvaluationTask, session: Session) -> dict:
        """Run run-tests.sh inside the container (injected after agent ends)
        then delegate to base class for file/trace collection."""
        grade_result = await self._run_grading(task)

        # Now let the base class collect /workspace and session trace
        info = await super()._collect_outputs(task, session)
        info["grade"] = grade_result

        # Persist grade alongside other task outputs
        grade_path = Path(task.output_dir) / "automated_grade.json"
        with open(grade_path, "w") as f:
            json.dump(grade_result, f, indent=2)
        logger.info(
            "Task %s grade: passed=%s (exit_code=%s)",
            task.id,
            grade_result.get("passed"),
            grade_result.get("exit_code"),
        )
        return info

    async def _run_grading(self, task: EvaluationTask) -> dict:
        """Inject run-tests.sh into the container and execute it.

        run-tests.sh is deliberately NOT available to the agent during execution
        (it would leak the answer).  We copy it in only after the agent finishes.
        """
        sample = task.sample
        task_dir = Path(sample["task_dir"])
        run_tests_sh = task_dir / "run-tests.sh"

        if not run_tests_sh.exists():
            logger.warning("run-tests.sh not found for task %s", task.id)
            return {
                "passed": None,
                "exit_code": None,
                "stdout": "",
                "stderr": "run-tests.sh not found on host",
                "timestamp": datetime.datetime.now().isoformat(),
            }

        sandbox = task.opensage_session.sandboxes.get_sandbox("main")

        # Step 1: inject the script AFTER agent session ends
        container_script = "/tmp/opensage_run_tests.sh"
        try:
            sandbox.copy_file_to_container(str(run_tests_sh), container_script)
        except Exception as e:
            logger.warning(
                "Failed to copy run-tests.sh into container for task %s: %s",
                task.id,
                e,
            )
            return {
                "passed": None,
                "exit_code": None,
                "stdout": "",
                "stderr": f"copy failed: {e}",
                "timestamp": datetime.datetime.now().isoformat(),
            }

        # Step 2: run it and capture output
        cmd = f"chmod +x {container_script} && bash {container_script}"
        try:
            stdout, exit_code = sandbox.run_command_in_container(
                cmd, timeout=self.grade_timeout_sec
            )
        except Exception as e:
            logger.warning("run-tests.sh execution failed for task %s: %s", task.id, e)
            return {
                "passed": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"execution error: {e}",
                "timestamp": datetime.datetime.now().isoformat(),
            }

        stdout_str = (
            stdout
            if isinstance(stdout, str)
            else stdout.decode("utf-8", errors="replace")
        )

        # Step 3: parse result — DevOps-Gym uses pytest-style summary lines
        passed = _parse_pytest_result(stdout_str, exit_code)

        return {
            "passed": passed,
            "exit_code": exit_code,
            "stdout": stdout_str,
            "stderr": "",
            "timestamp": datetime.datetime.now().isoformat(),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Aggregated evaluation
    # ──────────────────────────────────────────────────────────────────────────

    def evaluate(self) -> dict:
        """Scan output directory and aggregate per-task grades into a summary."""
        output_path = Path(self.output_dir)
        if not output_path.exists():
            logger.error("Output directory not found: %s", self.output_dir)
            return {}

        results: dict[str, dict] = {}
        missing_grade: list[str] = []

        for task_dir in sorted(output_path.iterdir()):
            if not task_dir.is_dir() or task_dir.name in ("results", "__pycache__"):
                continue
            grade_file = task_dir / "automated_grade.json"
            if not grade_file.exists():
                missing_grade.append(task_dir.name)
                continue
            with open(grade_file) as f:
                grade = json.load(f)
            results[task_dir.name] = grade

        total = len(results) + len(missing_grade)
        passed_ids = [tid for tid, g in results.items() if g.get("passed") is True]
        failed_ids = [tid for tid, g in results.items() if g.get("passed") is False]
        unknown_ids = [tid for tid, g in results.items() if g.get("passed") is None]

        # Per-category breakdown
        category_stats: dict[str, dict] = {}
        for tid, grade in results.items():
            # Infer category from task_id prefix pattern or grade metadata.
            # Grade doesn't store category, so derive from dataset if possible.
            cat = _infer_category(tid)
            if cat not in category_stats:
                category_stats[cat] = {"total": 0, "passed": 0}
            category_stats[cat]["total"] += 1
            if grade.get("passed"):
                category_stats[cat]["passed"] += 1

        success_rate = len(passed_ids) / total * 100 if total > 0 else 0.0

        sep = "=" * 60
        logger.warning(sep)
        logger.warning("DevOps-Gym Evaluation Results")
        logger.warning("Total tasks:   %d", total)
        logger.warning("Passed:        %d", len(passed_ids))
        logger.warning("Failed:        %d", len(failed_ids))
        logger.warning("Unknown:       %d", len(unknown_ids))
        logger.warning("Missing grade: %d", len(missing_grade))
        logger.warning("Success rate:  %.2f%%", success_rate)
        if category_stats:
            logger.warning("Per-category:")
            for cat, stats in sorted(category_stats.items()):
                cat_rate = (
                    stats["passed"] / stats["total"] * 100 if stats["total"] else 0
                )
                logger.warning(
                    "  %-20s %d/%d (%.1f%%)",
                    cat,
                    stats["passed"],
                    stats["total"],
                    cat_rate,
                )
        logger.warning(sep)

        summary = {
            "total": total,
            "passed": len(passed_ids),
            "failed": len(failed_ids),
            "unknown": len(unknown_ids),
            "missing_grade": len(missing_grade),
            "success_rate": success_rate,
            "passed_ids": sorted(passed_ids),
            "failed_ids": sorted(failed_ids),
            "unknown_ids": sorted(unknown_ids),
            "missing_grade_ids": sorted(missing_grade),
            "per_category": category_stats,
            "timestamp": datetime.datetime.now().isoformat(),
        }

        results_dir = output_path / "results"
        results_dir.mkdir(exist_ok=True)
        eval_file = results_dir / "evaluation_results.json"
        with open(eval_file, "w") as f:
            json.dump(summary, f, indent=2)
        logger.warning("Evaluation results saved to: %s", eval_file)

        return summary


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _parse_pytest_result(stdout: str, exit_code: int) -> bool:
    """Determine pass/fail from run-tests.sh output.

    DevOps-Gym scripts use pytest-style summary lines:
        PASSED build_test.py::test_build_success
        FAILED build_test.py::test_build_failure
    A task is considered passed if exit_code == 0 AND no FAILED lines appear.
    """
    if exit_code != 0:
        return False
    lines = stdout.splitlines()
    for line in lines:
        if re.search(r"\bFAILED\b", line):
            return False
    # Must have at least one PASSED line, or simply trust exit_code == 0
    return True


# Map known task_id prefixes/patterns to category names for the summary report.
_CATEGORY_PATTERNS = [
    (re.compile(r"^build_"), "build"),
    (re.compile(r"^real_world_repos__"), "monitor"),
    (re.compile(r"^end_to_end"), "end_to_end"),
]


def _infer_category(task_id: str) -> str:
    for pattern, cat in _CATEGORY_PATTERNS:
        if pattern.search(task_id):
            return cat
    # issue_resolving tasks look like "org__repo-number"
    if re.search(r"__.*-\d+$", task_id):
        return "issue_resolving / test_generation"
    return "unknown"


if __name__ == "__main__":
    fire.Fire(DevOpsGym)
