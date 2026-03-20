"""Ray-based distributed evaluation dispatcher.

Uses Ray actors for distributed evaluation across clusters.
Each EvalWorker actor runs one evaluation sample at a time,
reusing the Evaluation instance across samples.

Usage::

    from opensage.evaluation.dispatchers import get_dispatcher

    dispatcher = get_dispatcher("ray", ray_address="auto", max_workers=16)
    dispatcher.run(evaluation)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

import ray

from opensage.evaluation.dispatchers.base import BaseDispatcher

if TYPE_CHECKING:
    from opensage.evaluation.base import Evaluation

logger = logging.getLogger(__name__)


@ray.remote(num_cpus=0.25)
class EvalWorker:
    """Ray actor that runs evaluation samples.

    Each actor holds its own Evaluation instance and processes samples
    sequentially. Multiple actors run in parallel across the cluster.
    """

    def __init__(self, evaluation_pickle: bytes, agent_parent_dir: str | None = None):
        import os
        import sys

        if agent_parent_dir and os.path.isdir(agent_parent_dir):
            if agent_parent_dir not in sys.path:
                sys.path.insert(0, agent_parent_dir)
        else:
            from opensage.utils.project_info import PROJECT_PATH

            default_agents_dir = str(PROJECT_PATH / "examples" / "agents")
            if os.path.isdir(default_agents_dir) and default_agents_dir not in sys.path:
                sys.path.insert(0, default_agents_dir)

        import cloudpickle

        self._evaluation: Evaluation = cloudpickle.loads(evaluation_pickle)

        import litellm

        litellm.disable_streaming_logging = True
        litellm.success_callback = []
        litellm.failure_callback = []
        litellm.num_retries = self._evaluation.llm_retry_count
        litellm.request_timeout = self._evaluation.llm_retry_timeout

    def run_sample(self, sample: dict) -> dict:
        from opensage.evaluation.base import _run_sample_in_process

        result = _run_sample_in_process(self._evaluation, sample)

        task_id = result.get("task_id") or self._evaluation._get_task_id(sample)
        result["task_id"] = task_id
        output_dir = getattr(self._evaluation, "output_dir", "")
        if output_dir:
            task_dir = Path(output_dir) / task_id
            for p in [
                task_dir / "sandbox_output" / "prediction.patch",
                task_dir / "sandbox_output" / "workspace" / "prediction.patch",
            ]:
                if p.exists():
                    result["_patch_content"] = p.read_text()
                    break
            trace_path = task_dir / "session_trace.json"
            if trace_path.exists():
                result["_session_trace"] = trace_path.read_text()
        return result

    def get_worker_info(self) -> dict:
        import os
        import socket

        return {
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "node_ip": ray.util.get_node_ip_address(),
        }


class RayDispatcher(BaseDispatcher):
    """Distributed runner using Ray actors."""

    def __init__(
        self,
        ray_address: str = "auto",
        max_workers: int | None = None,
        **kwargs,
    ):
        self.ray_address = ray_address
        self.max_workers = max_workers

    def run(self, evaluation: Evaluation) -> None:
        import os

        import cloudpickle

        max_workers = self.max_workers or evaluation.max_workers

        if not ray.is_initialized():
            logger.info("Connecting to Ray cluster at %s", self.ray_address)
            env_vars = {
                k: v
                for k, v in os.environ.items()
                if k.startswith(("GOOGLE_", "OPENAI_", "ANTHROPIC_", "LITELLM_"))
            }
            ray.init(
                address=self.ray_address,
                logging_level=logging.WARNING,
                runtime_env={"env_vars": env_vars} if env_vars else None,
            )

        cluster_resources = ray.cluster_resources()
        logger.warning(
            "Ray cluster: %d CPUs, %.0f GB memory, %d nodes",
            int(cluster_resources.get("CPU", 0)),
            cluster_resources.get("memory", 0) / (1024**3),
            len(ray.nodes()),
        )

        evaluation.dataset = evaluation._get_dataset()
        samples = list(evaluation.dataset)
        total = len(samples)

        logger.warning(
            "Starting Ray evaluation: %d samples, %d workers",
            total,
            max_workers,
        )

        # Resolve relative paths before pickling
        for attr in ("config_template_path", "agent_dir", "output_dir"):
            val = getattr(evaluation, attr, None)
            if val:
                setattr(evaluation, attr, str(Path(val).resolve()))

        evaluation_bytes = cloudpickle.dumps(evaluation)
        agent_dir = getattr(evaluation, "agent_dir", None)
        agent_parent_dir = str(Path(agent_dir).parent) if agent_dir else None
        actors = [
            EvalWorker.remote(evaluation_bytes, agent_parent_dir)
            for _ in range(max_workers)
        ]

        worker_infos = ray.get([a.get_worker_info.remote() for a in actors])
        for i, info in enumerate(worker_infos):
            logger.info(
                "Worker %d: %s (pid=%d, ip=%s)",
                i,
                info["hostname"],
                info["pid"],
                info["node_ip"],
            )

        t0 = time.monotonic()
        results = []
        failed_samples = []
        completed = 0

        output_dir = Path(getattr(evaluation, "output_dir", ""))
        incremental_dir = output_dir / "_incremental" if output_dir else None
        if incremental_dir:
            incremental_dir.mkdir(parents=True, exist_ok=True)

        def _save_result_incremental(result: dict) -> None:
            if not incremental_dir:
                return
            task_id = result.get("task_id", "unknown")
            task_dir = output_dir / task_id
            task_dir.mkdir(parents=True, exist_ok=True)

            patch = result.pop("_patch_content", None)
            if patch is not None:
                patch_dir = task_dir / "sandbox_output"
                patch_dir.mkdir(parents=True, exist_ok=True)
                (patch_dir / "prediction.patch").write_text(patch)

            trace = result.pop("_session_trace", None)
            if trace is not None:
                (task_dir / "session_trace.json").write_text(trace)

            (task_dir / "result.json").write_text(
                json.dumps(result, indent=2, default=str)
            )

        pending = {}
        sample_queue = list(samples)
        sample_idx = 0

        def _submit_next(actor_idx: int) -> bool:
            nonlocal sample_idx
            if sample_idx >= len(sample_queue):
                return False
            sample = sample_queue[sample_idx]
            sample_idx += 1
            ref = actors[actor_idx].run_sample.remote(sample)
            pending[ref] = (actor_idx, sample)
            return True

        for actor_idx in range(max_workers):
            if not _submit_next(actor_idx):
                break

        def _rebuild_actors() -> None:
            nonlocal actors
            logger.warning(
                "Worker(s) lost. Creating %d pending actors to trigger autoscaler...",
                max_workers,
            )
            actors = [
                EvalWorker.remote(evaluation_bytes, agent_parent_dir)
                for _ in range(max_workers)
            ]
            for attempt in range(120):
                try:
                    ray.get([a.get_worker_info.remote() for a in actors], timeout=10)
                    logger.warning("All %d actors ready on new worker(s)", max_workers)
                    return
                except Exception:
                    if attempt % 6 == 0:
                        alive = [
                            n
                            for n in ray.nodes()
                            if n["Alive"] and n["Resources"].get("CPU", 0) > 0
                        ]
                        logger.warning(
                            "Waiting for actors to be scheduled... "
                            "(attempt %d/120, %d live worker node(s))",
                            attempt + 1,
                            len(alive),
                        )
            logger.error(
                "Actors not ready after 20 minutes, continuing with best effort"
            )

        while pending or sample_idx < len(sample_queue):
            if not pending:
                for actor_idx in range(max_workers):
                    if not _submit_next(actor_idx):
                        break
                if not pending:
                    break

            ready, _ = ray.wait(list(pending.keys()), num_returns=1)
            for ref in ready:
                actor_idx, sample = pending.pop(ref)
                completed += 1
                try:
                    result = ray.get(ref)
                    task_name = result.get("task_id", "unknown")
                    _save_result_incremental(result)
                    results.append(result)
                    logger.info(
                        "[%d/%d] Task %s completed (%.1fs elapsed)",
                        completed,
                        total,
                        task_name,
                        time.monotonic() - t0,
                    )
                    _submit_next(actor_idx)
                except Exception as e:
                    err_str = str(e)[:500]
                    is_worker_death = any(
                        pattern in err_str.lower()
                        for pattern in (
                            "unavailable",
                            "connection refused",
                            "actor died unexpectedly",
                            "worker died",
                            "node was terminated",
                            "rayactordied",
                        )
                    )
                    if is_worker_death:
                        logger.warning(
                            "Actor %d died (worker crash). Re-queuing sample and rebuilding...",
                            actor_idx,
                        )
                        sample_queue.append(sample)
                        remaining_refs = list(pending.keys())
                        for other_ref in remaining_refs:
                            _, other_sample = pending.pop(other_ref)
                            sample_queue.append(other_sample)
                            completed += 1
                        _rebuild_actors()
                        break
                    else:
                        failed_samples.append(err_str)
                        logger.error("[%d/%d] Task FAILED: %s", completed, total, e)
                        _submit_next(actor_idx)

        elapsed = time.monotonic() - t0

        evaluation.customized_modify_and_save_results(
            results=results,
            failed_samples=failed_samples,
            mode="ray",
        )

        logger.warning(
            "Ray evaluation complete: %d/%d succeeded in %.1fs (%.1f samples/min)",
            len(results),
            total,
            elapsed,
            len(results) / elapsed * 60 if elapsed > 0 else 0,
        )
        if failed_samples:
            logger.warning(
                "Failed samples (%d): %s",
                len(failed_samples),
                ", ".join(failed_samples),
            )
