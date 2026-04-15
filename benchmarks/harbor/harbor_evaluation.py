"""Harbor-compatible benchmark evaluation for OpenSage.

Runs any benchmark that uses Harbor's standard task directory format:
    task_dir/
    ├── instruction.md          # agent prompt
    ├── task.toml               # timeout, resources config
    ├── environment/Dockerfile  # container image
    └── tests/test.sh           # verification script (exit 0 = pass)

This includes SWE-bench, CompileBench, GAIA, and 60+ other benchmarks
from the Harbor ecosystem.

Usage:
    # Local tasks directory
    python -m benchmarks.harbor.harbor_evaluation run \\
        --dataset_path /path/to/harbor/tasks \\
        --agent_dir examples/agents/harbor_agent

    # Auto-download from harbor registry (requires: pip install harbor)
    python -m benchmarks.harbor.harbor_evaluation run \\
        --dataset_path swebench \\
        --agent_dir examples/agents/harbor_agent
"""

import datetime
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import datasets
import docker
import fire

try:
    import toml
except ImportError:
    toml = None

from opensage.evaluation.base import Evaluation, EvaluationTask
from opensage.session import get_opensage_session
from opensage.utils.project_info import PROJECT_PATH

logger = logging.getLogger(__name__)

# Harbor's default cache directory
HARBOR_TASKS_CACHE = Path.home() / ".cache" / "harbor" / "tasks"


def _parse_task_toml(path: Path) -> dict:
    """Parse task.toml, return empty dict if missing or unparseable."""
    if not path.exists():
        return {}
    if toml is not None:
        with open(path) as f:
            return toml.load(f)
    # Fallback: basic key=value parsing for timeout
    config = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("[") and not line.startswith("#"):
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip().strip('"').strip("'")
    return config


def _resolve_tasks_path(dataset_path: str) -> Path:
    """Resolve dataset_path to a local directory.

    If dataset_path is a local directory, return it directly.
    Otherwise, treat it as a harbor registry name and download
    to ~/.cache/harbor/tasks/ using `harbor download`.
    """
    local = Path(dataset_path)
    if local.is_dir():
        return local

    # Check if already cached by harbor
    cached = HARBOR_TASKS_CACHE / dataset_path
    if cached.is_dir():
        logger.info(f"Using cached harbor tasks: {cached}")
        return cached

    # Try harbor download
    if shutil.which("harbor") is None:
        raise FileNotFoundError(
            f"'{dataset_path}' is not a local directory and `harbor` CLI is not installed. "
            f"Either provide a local path or install harbor: pip install harbor"
        )

    logger.warning(f"Downloading harbor tasks: {dataset_path}")
    result = subprocess.run(
        ["harbor", "download", dataset_path, "-o", str(cached)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"harbor download failed:\n{result.stderr or result.stdout}")

    if not cached.is_dir():
        raise FileNotFoundError(
            f"harbor download succeeded but tasks not found at {cached}"
        )

    logger.warning(f"Downloaded harbor tasks to: {cached}")
    return cached


@dataclass(kw_only=True)
class HarborEvaluation(Evaluation):
    """Generic Harbor-format benchmark evaluation.

    Reads task directories in Harbor's standard format and runs them
    with OpenSage agents. Compatible with any Harbor adapter output.

    Set dataset_path to either:
    - A local directory containing Harbor task subdirectories
    - A harbor registry dataset name (auto-downloaded via `harbor download`)

    Each task subdirectory must contain:
    - instruction.md: Agent prompt
    - environment/Dockerfile: Container image definition
    - tests/test.sh: Verification script (exit 0 = pass)
    - task.toml (optional): Timeout and resource configuration
    """

    dataset_path: str = ""
    name: str = "harbor"
    agent_dir: str = str(PROJECT_PATH / "examples/agents/harbor_agent")
    config_template_path: str = str(
        PROJECT_PATH / "examples/agents/harbor_agent/config.toml"
    )
    max_llm_calls: int = 200
    run_until_explicit_finish: bool = True

    # Filtering
    start_idx: int = 0
    end_idx: int | None = None
    task_file: str | None = None
    """Path to file with task IDs to run (one per line)"""
    skip_existing: bool = False

    # Test execution
    test_timeout: int = 120
    """Default timeout for running tests (seconds), overridden by task.toml"""

    def __post_init__(self):
        if not self.dataset_path:
            raise ValueError(
                "dataset_path is required (local dir or harbor dataset name)"
            )
        # Resolve to local path (downloads if needed)
        self._resolved_tasks_path = _resolve_tasks_path(self.dataset_path)
        super().__post_init__()

    # ========= Dataset loading =========

    def _get_dataset(self) -> datasets.Dataset:
        """Build dataset by scanning Harbor task directories."""
        tasks_path = self._resolved_tasks_path
        samples = []

        for task_dir in sorted(tasks_path.iterdir()):
            if not task_dir.is_dir():
                continue

            instruction_file = task_dir / "instruction.md"
            dockerfile = task_dir / "environment" / "Dockerfile"

            if not instruction_file.exists():
                logger.debug(f"Skipping {task_dir.name}: no instruction.md")
                continue
            if not dockerfile.exists():
                logger.debug(f"Skipping {task_dir.name}: no environment/Dockerfile")
                continue

            instruction = instruction_file.read_text().strip()
            if not instruction:
                logger.warning(f"Skipping {task_dir.name}: empty instruction.md")
                continue

            # Parse task.toml for config
            task_config = _parse_task_toml(task_dir / "task.toml")
            verifier_config = task_config.get("verifier", {})
            agent_config = task_config.get("agent", {})

            samples.append(
                {
                    "task_id": task_dir.name,
                    "description": instruction,
                    "task_dir": str(task_dir),
                    "agent_timeout_sec": int(agent_config.get("timeout_sec", 300)),
                    "test_timeout_sec": int(
                        verifier_config.get("timeout_sec", self.test_timeout)
                    ),
                }
            )

        if not samples:
            raise ValueError(f"No valid Harbor tasks found in {tasks_path}")

        logger.warning(f"Found {len(samples)} Harbor tasks in {tasks_path}")
        dataset = datasets.Dataset.from_list(samples)

        # Apply filtering
        if self.task_file:
            task_file_path = Path(self.task_file)
            if task_file_path.exists():
                with open(task_file_path) as f:
                    task_ids = {line.strip() for line in f if line.strip()}
                dataset = dataset.filter(lambda x: x["task_id"] in task_ids)
                logger.warning(
                    f"Filtered to {len(dataset)} tasks from {self.task_file}"
                )

        if self.end_idx is not None:
            dataset = dataset.select(
                range(self.start_idx, min(self.end_idx, len(dataset)))
            )
        elif self.start_idx > 0:
            dataset = dataset.select(range(self.start_idx, len(dataset)))

        if self.skip_existing and Path(self.output_dir).exists():
            existing = {
                d.name
                for d in Path(self.output_dir).iterdir()
                if d.is_dir() and d.name not in ("results", "__pycache__")
            }
            if existing:
                pre = len(dataset)
                dataset = dataset.filter(lambda x: x["task_id"] not in existing)
                logger.warning(f"Skipped {pre - len(dataset)} existing tasks")

        logger.warning(f"Running {len(dataset)} tasks")
        return dataset

    # ========= Abstract method implementations =========

    def _get_task_id(self, sample: dict) -> str:
        return sample["task_id"]

    def _get_first_user_message(self, sample: dict) -> str:
        return sample["description"]

    def _get_export_dir_in_sandbox(self, sample: dict) -> str | tuple | None:
        return "/app"

    # ========= Config template variables =========

    def _get_config_template_variables(self, task: EvaluationTask) -> dict:
        tmpl_vars = super()._get_config_template_variables(task)
        tmpl_vars["DEFAULT_IMAGE"] = f"harbor_{task.id}"
        return tmpl_vars

    # ========= Docker image building =========

    def _before_generate_one_callback(self, task: EvaluationTask):
        """Build Docker image from the task's environment/Dockerfile."""
        task_dir = Path(task.sample["task_dir"])
        dockerfile_dir = task_dir / "environment"
        image_tag = f"harbor_{task.id}"

        logger.warning(f"Building Docker image '{image_tag}' from {dockerfile_dir}")
        client = docker.from_env()
        try:
            _, build_logs = client.images.build(
                path=str(dockerfile_dir),
                tag=image_tag,
                rm=True,
            )
            for log_entry in build_logs:
                if "stream" in log_entry:
                    line = log_entry["stream"].strip()
                    if line:
                        logger.debug(f"[docker build] {line}")
            logger.warning(f"Successfully built image: {image_tag}")
        except docker.errors.BuildError as e:
            logger.error(f"Failed to build image for task {task.id}: {e}")
            raise

    # ========= Test execution and evaluation =========

    async def _collect_outputs(self, task: EvaluationTask, session) -> dict:
        """Run Harbor's tests/test.sh for verification."""
        info = await super()._collect_outputs(task, session)

        test_result = self._run_task_tests(task)
        info["test_result"] = test_result

        test_result_file = Path(task.output_dir) / "test_result.json"
        with open(test_result_file, "w") as f:
            json.dump(test_result, f, indent=2)
        logger.warning(
            f"Task {task.id}: test {'PASSED' if test_result['passed'] else 'FAILED'}"
        )

        return info

    def _run_task_tests(self, task: EvaluationTask) -> dict:
        """Copy and execute tests/test.sh inside the container."""
        opensage_session = get_opensage_session(task.session_id)
        sandbox = opensage_session.sandboxes.get_sandbox("main")

        task_dir = Path(task.sample["task_dir"])
        tests_dir = task_dir / "tests"

        if not tests_dir.exists():
            logger.warning(f"No tests/ directory found for task {task.id}")
            return {
                "task_id": task.id,
                "passed": False,
                "exit_code": -1,
                "output": "No tests/ directory found",
                "error": "missing_tests",
            }

        # Copy tests/ into the container
        sandbox.copy_directory_to_container(
            src_path=str(tests_dir),
            dst_path="/tests",
        )

        # Run test.sh (Harbor standard) or fallback to pytest
        test_sh = tests_dir / "test.sh"
        if test_sh.exists():
            sandbox.copy_file_to_container(
                local_path=str(test_sh),
                container_path="/tests/test.sh",
            )
            test_cmd = "chmod +x /tests/test.sh && /tests/test.sh"
        else:
            test_cmd = "pip install -q pytest && python -m pytest /tests/ -v --tb=short"

        test_timeout = task.sample.get("test_timeout_sec", self.test_timeout)
        output, exit_code = sandbox.run_command_in_container(
            test_cmd,
            timeout=test_timeout,
        )

        return {
            "task_id": task.id,
            "passed": exit_code == 0,
            "exit_code": exit_code,
            "output": (
                output
                if isinstance(output, str)
                else output.decode("utf-8", errors="replace")
            ),
        }

    # ========= Reward function for RL integration =========

    @staticmethod
    async def reward_func(args, sample, **kwargs) -> dict:
        """Compute reward from test results."""
        metadata = (
            getattr(sample, "metadata", {}) if not isinstance(sample, dict) else sample
        )
        test_result = metadata.get("test_result", {})
        passed = test_result.get("passed", False)
        return {"score": 1.0 if passed else 0.0}

    # ========= Aggregated evaluation =========

    def evaluate(self) -> dict:
        """Aggregate test results across all tasks."""
        results = []
        output_path = Path(self.output_dir)

        for task_dir in sorted(output_path.iterdir()):
            test_result_file = task_dir / "test_result.json"
            if not test_result_file.exists():
                continue
            with open(test_result_file) as f:
                results.append(json.load(f))

        total = len(results)
        passed = sum(1 for r in results if r["passed"])
        failed = total - passed
        pass_rate = (passed / total * 100) if total > 0 else 0

        logger.warning("=" * 60)
        logger.warning("Harbor Evaluation Results")
        logger.warning(f"Total tasks: {total}")
        logger.warning(f"Passed: {passed}")
        logger.warning(f"Failed: {failed}")
        logger.warning(f"Pass rate: {pass_rate:.2f}%")
        logger.warning("=" * 60)

        passed_tasks = [r["task_id"] for r in results if r["passed"]]
        failed_tasks = [r["task_id"] for r in results if not r["passed"]]
        if passed_tasks:
            logger.warning(f"Passed tasks: {passed_tasks}")
        if failed_tasks:
            logger.warning(f"Failed tasks: {failed_tasks}")

        eval_results = {
            "total_tasks": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": pass_rate,
            "passed_tasks": passed_tasks,
            "failed_tasks": failed_tasks,
            "per_task_results": results,
            "timestamp": datetime.datetime.now().isoformat(),
        }

        eval_file = output_path / "evaluation_results.json"
        with open(eval_file, "w") as f:
            json.dump(eval_results, f, indent=2)
        logger.warning(f"Evaluation results saved to: {eval_file}")

        return eval_results


if __name__ == "__main__":
    fire.Fire(HarborEvaluation)
