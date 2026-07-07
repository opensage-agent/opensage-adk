from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import datasets

from opensage.evaluation.base import Evaluation, EvaluationTask
from opensage.evaluation.dispatchers.native import NativeDispatcher


@dataclass(kw_only=True)
class _LoggingSmokeEvaluation(Evaluation):
    dataset_path: str = ""
    agent_dir: str = "."
    config_template_path: str = ""
    name: str = "logging_smoke"
    max_workers: int = 1
    non_interactive: bool = True

    def __post_init__(self) -> None:
        self._terminal_log_level = 20

    def _get_dataset(self):
        return datasets.Dataset.from_list([{"id": "task-one"}])

    def _create_task(self, sample: dict, model=None) -> EvaluationTask:
        return EvaluationTask(
            id=sample["id"],
            sample=sample,
            first_user_message="hello",
            output_dir=str(Path(self.output_dir) / sample["id"]),
            model=model,
        )

    async def _generate_one(self, task: EvaluationTask) -> dict:
        return {"task_id": task.id}

    def _get_task_id(self, sample: dict) -> str:
        return sample["id"]

    def _get_first_user_message(self, sample: dict) -> str:
        return "hello"

    def _get_export_dir_in_sandbox(self, sample: dict):
        return None

    def evaluate(self):
        return {}


def test_native_single_thread_writes_execution_logs(tmp_path: Path) -> None:
    evaluation = object.__new__(_LoggingSmokeEvaluation)
    evaluation.output_dir = str(tmp_path)
    evaluation.max_workers = 1
    evaluation.llm_retry_count = 3
    evaluation.llm_retry_timeout = 30
    evaluation._terminal_log_level = 20

    NativeDispatcher(max_workers=1).run(evaluation)

    task_dir = tmp_path / "task-one"
    assert (task_dir / "execution_debug.log").exists()
    assert (task_dir / "execution_info.log").exists()
