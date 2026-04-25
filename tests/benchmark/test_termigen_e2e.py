"""End-to-end test for :class:`TermigenBench`.

Exercises the full benchmark pipeline:

    TermigenBench(dataset_path=...) -> _get_dataset ->
    build Docker image from environment/Dockerfile -> run agent (real LLM) ->
    copy tests/ into container -> execute test.sh ->
    aggregate evaluation_results.json

A synthetic Harbor 2.0 task is injected into ``tmp_path`` so we skip the
~650 MB upstream clone; the loader + Harbor pipeline logic is otherwise
untouched. The task instructs the agent to write ``hello world`` to
``/app/answer.txt`` — trivial enough that any capable model solves it in a
single tool call.

Prerequisites (auto-skipped if missing):
    - Docker daemon reachable
    - ``OPENAI_API_KEY`` set (harbor_agent's default model is ``openai/gpt-4o``;
      we keep that default so no agent config rewriting is needed)

Run manually:

    OPENAI_API_KEY=sk-... \\
        pytest tests/benchmark/test_termigen_e2e.py -v -s -m slow
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Prerequisite probes — evaluated at import time so skip reasons show up in
# the collection report.
# ---------------------------------------------------------------------------

try:
    import docker as _docker

    try:
        _docker.from_env().ping()
        _DOCKER_REASON: str | None = None
    except Exception as exc:  # pragma: no cover - environment dependent
        _DOCKER_REASON = f"Docker daemon not reachable: {exc}"
except ImportError as exc:  # pragma: no cover
    _DOCKER_REASON = f"docker SDK not importable: {exc}"

_HAS_OPENAI_KEY = bool(os.environ.get("OPENAI_API_KEY"))

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(_DOCKER_REASON is not None, reason=_DOCKER_REASON or ""),
    pytest.mark.skipif(
        not _HAS_OPENAI_KEY,
        reason="OPENAI_API_KEY not set",
    ),
]


SYNTHETIC_TASK_ID = "termigen_e2e_smoke"
SYNTHETIC_INSTRUCTION = (
    "Create a file at /app/answer.txt whose contents are exactly the three "
    "words `hello world` on a single line, then call the finish_task tool. "
    "Do not add quotes, punctuation, or extra whitespace."
)
# Tiny fast-to-pull base that still has the apk package manager used by the
# opensage main Dockerfile layer.
SYNTHETIC_BASE_IMAGE = "alpine:3.19"


def _write_synthetic_task(tasks_root: Path) -> None:
    """Lay out a minimal Harbor 2.0 task on disk."""
    task_dir = tasks_root / SYNTHETIC_TASK_ID
    (task_dir / "environment").mkdir(parents=True)
    (task_dir / "tests").mkdir(parents=True)

    (task_dir / "instruction.md").write_text(SYNTHETIC_INSTRUCTION + "\n")

    (task_dir / "environment" / "Dockerfile").write_text(
        f"FROM {SYNTHETIC_BASE_IMAGE}\nRUN mkdir -p /app\n"
    )

    # newline-stripped grep so "hello world\n" also matches.
    test_sh = task_dir / "tests" / "test.sh"
    test_sh.write_text(
        "#!/bin/sh\n"
        'actual="$(cat /app/answer.txt 2>/dev/null | tr -d "\\r" )"\n'
        'expected="hello world"\n'
        'test "$actual" = "$expected"\n'
    )
    test_sh.chmod(0o755)

    (task_dir / "task.toml").write_text(
        "[verifier]\ntimeout_sec = 60\n\n[agent]\ntimeout_sec = 300\n"
    )


@pytest.mark.timeout(900)
def test_termigen_bench_runs_single_task_end_to_end(tmp_path):
    """Full pipeline smoke: build image → agent writes file → test.sh passes."""
    from benchmarks.termigen import TermigenBench

    tasks_root = tmp_path / "environments_harbor"
    tasks_root.mkdir()
    _write_synthetic_task(tasks_root)

    output_dir = tmp_path / "output"

    bench = TermigenBench(
        dataset_path=str(tasks_root),
        output_dir=str(output_dir),
        max_workers=1,
        max_llm_calls=30,
        non_interactive=True,
    )

    # run_debug() = NativeDispatcher (single worker, no multiprocessing) + evaluate().
    bench.run_debug()

    # 1. Per-task test result file must be produced.
    test_result_path = output_dir / SYNTHETIC_TASK_ID / "test_result.json"
    assert test_result_path.exists(), (
        f"Expected {test_result_path} but the Harbor pipeline did not write it. "
        f"Contents of {output_dir}: "
        f"{[p.name for p in output_dir.iterdir()] if output_dir.exists() else 'missing'}"
    )
    test_result = json.loads(test_result_path.read_text())
    assert test_result["task_id"] == SYNTHETIC_TASK_ID
    assert "passed" in test_result
    assert "exit_code" in test_result

    # 2. Aggregated evaluation_results.json must be produced.
    eval_path = output_dir / "evaluation_results.json"
    assert eval_path.exists()
    eval_results = json.loads(eval_path.read_text())
    assert eval_results["total_tasks"] == 1
    assert eval_results["passed"] + eval_results["failed"] == 1
    assert len(eval_results["per_task_results"]) == 1
    assert eval_results["per_task_results"][0]["task_id"] == SYNTHETIC_TASK_ID

    # 3. The task is so trivial that failure almost always indicates a real
    #    regression (agent wiring, Docker build, test.sh invocation) rather
    #    than model weakness. If this flakes, inspect `test_result["output"]`
    #    and the session_trace under the task directory.
    assert test_result["passed"], (
        "Agent failed the trivial hello-world task. test.sh output:\n"
        f"{test_result.get('output', '')[:4000]}"
    )
