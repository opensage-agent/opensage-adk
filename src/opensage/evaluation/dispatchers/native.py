"""Native evaluation dispatcher — single-thread, threading, or multiprocessing."""

from __future__ import annotations

import asyncio
import logging
import traceback
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from tqdm import tqdm

from opensage.evaluation.dispatchers.base import BaseDispatcher

if TYPE_CHECKING:
    from opensage.evaluation.base import Evaluation

logger = logging.getLogger(__name__)


class NativeDispatcher(BaseDispatcher):
    """Runs evaluation samples on the local machine.

    Selects between single-thread or multiprocessing based on ``max_workers``.
    """

    def __init__(
        self,
        max_workers: int = 6,
    ):
        self.max_workers = max_workers

    def run(self, evaluation: Evaluation) -> None:
        if self.max_workers == 1:
            self._run_single_thread(evaluation)
        else:
            self._run_multiprocess(evaluation)

    # ------------------------------------------------------------------ #

    def _run_single_thread(self, evaluation: Evaluation) -> None:
        """Generate samples sequentially in a single thread."""
        evaluation.dataset = evaluation._get_dataset()
        results = []
        failed_samples = []

        for sample in tqdm(
            evaluation.dataset, desc="Generating samples (single-threaded)"
        ):
            task_name = evaluation._get_task_id(sample)
            try:
                task = evaluation._create_task(sample)
                result = asyncio.run(evaluation._generate_one(task))
                results.append(result)
                logger.info(f"✓ Task {task_name} completed")
            except Exception as e:
                failed_samples.append(task_name)
                logger.error(f"✗ Task {task_name} FAILED")
                logger.error(f"  Error: {e}")
                logger.error(f"  Traceback:\n{traceback.format_exc()}")

        evaluation.customized_modify_and_save_results(
            results=results,
            failed_samples=failed_samples,
            mode="single_thread",
        )
        logger.warning(
            f"Generated {len(results)}/{len(evaluation.dataset)} samples successfully"
        )
        if failed_samples:
            logger.warning(
                f"Failed samples ({len(failed_samples)}): {', '.join(failed_samples)}"
            )

    # ------------------------------------------------------------------ #

    def _run_multiprocess(self, evaluation: Evaluation) -> None:
        """Generate samples using multiprocessing for true parallelism."""
        from pathlib import Path

        from opensage.evaluation.base import _run_sample_in_process

        evaluation.dataset = evaluation._get_dataset()

        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(_run_sample_in_process, evaluation, sample): sample
                for sample in evaluation.dataset
            }

            results = []
            failed_samples = []

            for future in tqdm(
                as_completed(futures),
                total=len(evaluation.dataset),
                desc="Generating samples (multiprocess)",
            ):
                sample = futures[future]
                task_name = evaluation._get_task_id(sample)

                try:
                    result = future.result()
                    results.append(result)
                    logger.info(f"✓ Task {task_name} completed successfully")
                except Exception as e:
                    failed_samples.append(task_name)
                    logger.error(f"✗ Task {task_name} FAILED")
                    logger.error(f"  Error: {e}")
                    logger.error(f"  Traceback:\n{traceback.format_exc()}")

                    error_file = Path(evaluation.output_dir) / task_name / "error.json"
                    if error_file.exists():
                        logger.error(f"  Detailed error saved to: {error_file}")

        evaluation.customized_modify_and_save_results(
            results=results,
            failed_samples=failed_samples,
            mode="multiprocess",
        )
        logger.warning(
            f"Generated {len(results)}/{len(evaluation.dataset)} samples successfully"
        )
        if failed_samples:
            logger.warning(
                f"Failed samples ({len(failed_samples)}): {', '.join(failed_samples)}"
            )
