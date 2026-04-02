import datetime
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import datasets
import docker
import fire
import yaml

from opensage.evaluation.base import Evaluation, EvaluationTask
from opensage.session import get_opensage_session
from opensage.utils.project_info import PROJECT_PATH

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class TerminalBench(Evaluation):
    """TerminalBench 2.0 benchmark evaluation.

    Evaluates AI agents on real-world terminal tasks. Each task provides a Docker
    environment and a test script to verify task completion.

    Requires a local clone of the terminal-bench repository:
        git clone https://github.com/laude-institute/terminal-bench.git

    Usage:
        python -m benchmarks.terminal_bench.terminal_bench run \\
            --tb_repo_dir /path/to/terminal-bench \\
            --agent_dir examples/agents/terminal_bench_agent \\
            --max_workers 1 --end_idx 1
    """

    # TB repo path (required)
    tb_repo_dir: str
    """Path to local clone of terminal-bench repository"""

    # Override Evaluation defaults
    dataset_path: str = ""  # Not used; we override _get_dataset()
    name: str = "terminal_bench"
    agent_dir: str = str(PROJECT_PATH / "examples/agents/terminal_bench_agent")
    config_template_path: str = str(
        PROJECT_PATH / "examples/agents/terminal_bench_agent/config.toml"
    )
    max_llm_calls: int = 200
    run_until_explicit_finish: bool = True

    # TB-specific configs
    test_timeout: int = 60
    """Timeout in seconds for running pytest inside the container"""

    difficulty: str = "base"
    """Task description difficulty level from task.yaml (e.g., 'base', 'hard')"""

    # Filtering
    start_idx: int = 0
    end_idx: int | None = None
    task_file: str | None = None
    """Path to file with task IDs to run (one per line)"""
    skip_existing: bool = False

    def __post_init__(self):
        tb_path = Path(self.tb_repo_dir)
        if not tb_path.exists():
            raise FileNotFoundError(
                f"TB repo directory not found: {self.tb_repo_dir}. "
                f"Please clone: git clone https://github.com/laude-institute/terminal-bench.git"
            )
        tasks_dir = tb_path / "tasks"
        if not tasks_dir.exists():
            raise FileNotFoundError(
                f"No tasks/ directory found in {self.tb_repo_dir}. "
                f"Is this a valid terminal-bench repository?"
            )
        super().__post_init__()

    # ========= Dataset loading =========

    def _get_dataset(self) -> datasets.Dataset:
        """Build dataset by scanning tb_repo_dir/tasks/ directory."""
        tasks_dir = Path(self.tb_repo_dir) / "tasks"
        samples = []

        for task_dir in sorted(tasks_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            task_yaml = task_dir / "task.yaml"
            dockerfile = task_dir / "Dockerfile"
            if not task_yaml.exists() or not dockerfile.exists():
                logger.debug(f"Skipping {task_dir.name}: missing task.yaml or Dockerfile")
                continue

            with open(task_yaml, "r") as f:
                task_config = yaml.safe_load(f)

            # Extract description at the requested difficulty level
            descriptions = task_config.get("descriptions", {})
            description = descriptions.get(self.difficulty)
            if description is None:
                description = descriptions.get("base")
            if description is None:
                # Fallback: use the first available description
                description = next(iter(descriptions.values()), None) if descriptions else None
            if description is None:
                logger.warning(f"Skipping {task_dir.name}: no description found in task.yaml")
                continue

            samples.append({
                "task_id": task_dir.name,
                "description": description,
                "task_dir": str(task_dir),
                "max_agent_timeout_sec": task_config.get("max_agent_timeout_sec", 180),
                "max_test_timeout_sec": task_config.get("max_test_timeout_sec", 30),
            })

        if not samples:
            raise ValueError(f"No valid tasks found in {tasks_dir}")

        logger.warning(f"Found {len(samples)} tasks in {tasks_dir}")
        dataset = datasets.Dataset.from_list(samples)

        # Apply filtering
        if self.task_file:
            task_file_path = Path(self.task_file)
            if task_file_path.exists():
                with open(task_file_path, "r") as f:
                    task_ids = set(line.strip() for line in f if line.strip())
                dataset = dataset.filter(lambda x: x["task_id"] in task_ids)
                logger.warning(f"Filtered to {len(dataset)} tasks from {self.task_file}")

        if self.end_idx is not None:
            dataset = dataset.select(range(self.start_idx, min(self.end_idx, len(dataset))))
        elif self.start_idx > 0:
            dataset = dataset.select(range(self.start_idx, len(dataset)))

        if self.skip_existing and Path(self.output_dir).exists():
            existing = {
                d.name for d in Path(self.output_dir).iterdir()
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
        tmpl_vars["DEFAULT_IMAGE"] = f"tb_{task.id}"
        return tmpl_vars

    # ========= Docker image building =========

    def _before_generate_one_callback(self, task: EvaluationTask):
        """Build the Docker image for this task from its Dockerfile."""
        task_dir = task.sample["task_dir"]
        image_tag = f"tb_{task.id}"

        logger.warning(f"Building Docker image '{image_tag}' from {task_dir}")
        client = docker.from_env()
        try:
            _, build_logs = client.images.build(
                path=task_dir,
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
        """Collect outputs and run TB's test scripts inside the container."""
        # First, run the standard output collection
        info = await super()._collect_outputs(task, session)

        # Then run TB's test scripts
        test_result = self._run_task_tests(task)
        info["test_result"] = test_result

        # Save test result to task output dir
        test_result_file = Path(task.output_dir) / "test_result.json"
        with open(test_result_file, "w") as f:
            json.dump(test_result, f, indent=2)
        logger.warning(
            f"Task {task.id}: test {'PASSED' if test_result['passed'] else 'FAILED'}"
        )

        return info

    def _run_task_tests(self, task: EvaluationTask) -> dict:
        """Copy test scripts into the container and run pytest."""
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

        # Copy tests/ into the container at /tests/
        sandbox.copy_directory_to_container(
            src_path=str(tests_dir),
            dst_path="/tests",
        )
        logger.info(f"Copied tests/ to container for task {task.id}")

        # Copy run-tests.sh if it exists, otherwise use default
        run_tests_sh = task_dir / "run-tests.sh"
        if run_tests_sh.exists():
            sandbox.copy_file_to_container(
                local_path=str(run_tests_sh),
                container_path="/run-tests.sh",
            )
            test_cmd = "chmod +x /run-tests.sh && TEST_DIR=/tests/ /run-tests.sh"
        else:
            test_cmd = (
                "pip install -q pytest && "
                "TEST_DIR=/tests/ python -m pytest /tests/test_outputs.py -v --tb=short"
            )

        test_timeout = task.sample.get("max_test_timeout_sec", self.test_timeout)
        output, exit_code = sandbox.run_command_in_container(
            test_cmd,
            timeout=test_timeout,
        )

        return {
            "task_id": task.id,
            "passed": exit_code == 0,
            "exit_code": exit_code,
            "output": output if isinstance(output, str) else output.decode("utf-8", errors="replace"),
        }

    def evaluate(self) -> dict:
        """Aggregate test results across all tasks."""
        results = []
        output_path = Path(self.output_dir)

        for task_dir in sorted(output_path.iterdir()):
            test_result_file = task_dir / "test_result.json"
            if not test_result_file.exists():
                continue
            with open(test_result_file, "r") as f:
                results.append(json.load(f))

        total = len(results)
        passed = sum(1 for r in results if r["passed"])
        failed = total - passed
        pass_rate = (passed / total * 100) if total > 0 else 0

        logger.warning("=" * 60)
        logger.warning("TerminalBench Evaluation Results")
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
    fire.Fire(TerminalBench)
