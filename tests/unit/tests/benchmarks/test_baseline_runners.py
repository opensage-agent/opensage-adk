from __future__ import annotations

import inspect
import json
import threading
import time
from pathlib import Path

from benchmarks.cybench import helpers as cy_helpers
from benchmarks.cybench.baseline import (
    BudgetSnapshot as CyBudgetSnapshot,
)
from benchmarks.cybench.baseline import (
    BudgetTracker as CyBudgetTracker,
)
from benchmarks.cybench.baseline import (
    CyBenchBaseline,
)
from benchmarks.cybench.baseline import (
    ProcessResult as CyProcessResult,
)
from benchmarks.cybench.baseline import (
    jsonl_to_session_trace as cy_jsonl_to_session_trace,
)
from benchmarks.nyuctf.baseline import (
    BudgetSnapshot as NYUBudgetSnapshot,
)
from benchmarks.nyuctf.baseline import NYUCTFBaseline
from benchmarks.nyuctf.baseline import (
    ProcessResult as NYUProcessResult,
)
from benchmarks.nyuctf.helpers import LoadedChallenge


def test_baseline_model_options_are_not_constructor_args() -> None:
    for runner_cls in (CyBenchBaseline, NYUCTFBaseline):
        parameters = inspect.signature(runner_cls).parameters

        assert "agent" in parameters
        assert "provider" not in parameters
        assert "model" not in parameters
        assert "reasoning_effort" not in parameters


def test_nyuctf_baseline_hides_submission_metadata_options() -> None:
    hidden = {
        "submission_agent",
        "submission_model",
        "submission_link",
        "submission_comment",
    }

    assert hidden.isdisjoint(inspect.signature(NYUCTFBaseline).parameters)


def test_cybench_claude_baseline_defaults_and_command(tmp_path: Path) -> None:
    runner = CyBenchBaseline(
        agent="claude",
        output_dir=str(tmp_path / "out"),
        execution_mode="local",
        env_file="",
    )

    assert runner.model == "claude-opus-4-8"
    assert runner.reasoning_effort == "high"
    assert runner.budget == 100.0

    command = runner._agent_shell_command(
        prompt_path=tmp_path / "prompt.txt",
        shared_dir=tmp_path / "shared",
        workspace_dir=tmp_path / "workspace",
    )

    assert "--model claude-opus-4-8" in command
    assert "--effort high" in command
    assert "--max-budget-usd 100.0" in command
    assert "--output-format stream-json" in command


def test_cybench_codex_baseline_defaults_and_command(tmp_path: Path) -> None:
    runner = CyBenchBaseline(
        agent="codex",
        output_dir=str(tmp_path / "out"),
        execution_mode="local",
        env_file="",
    )

    assert runner.model == "gpt-5.5"
    assert runner.reasoning_effort == "high"
    assert runner.budget == 100.0

    command = runner._agent_shell_command(
        prompt_path=tmp_path / "prompt.txt",
        shared_dir=tmp_path / "shared",
        workspace_dir=tmp_path / "workspace",
    )

    assert "codex exec --json" in command
    assert "--model gpt-5.5" in command
    assert "--config model_reasoning_effort='\"high\"'" in command
    assert "--dangerously-bypass-approvals-and-sandbox" in command


def test_nyuctf_baseline_defaults(tmp_path: Path) -> None:
    claude = NYUCTFBaseline(
        agent="claude",
        output_dir=str(tmp_path / "claude"),
        execution_mode="local",
        env_file="",
    )
    codex = NYUCTFBaseline(
        agent="codex",
        output_dir=str(tmp_path / "codex"),
        execution_mode="local",
        env_file="",
    )

    assert claude.model == "claude-opus-4-8"
    assert codex.model == "gpt-5.5"
    assert claude.reasoning_effort == "high"
    assert codex.reasoning_effort == "high"
    assert claude.budget == 100.0
    assert codex.budget == 100.0


def test_baseline_validates_max_workers(tmp_path: Path) -> None:
    for runner_cls in (CyBenchBaseline, NYUCTFBaseline):
        try:
            runner_cls(
                agent="codex",
                output_dir=str(tmp_path / runner_cls.__name__),
                execution_mode="local",
                env_file="",
                max_workers=0,
            )
        except ValueError as exc:
            assert "max_workers" in str(exc)
        else:
            raise AssertionError("expected max_workers validation failure")


def test_cybench_baseline_run_uses_max_workers(tmp_path: Path, monkeypatch) -> None:
    tasks = [
        cy_helpers.LoadedCybenchTask(
            canonical_name=f"toy-{index}",
            relative_task_dir=f"benchmark/demo/ctf/misc/toy-{index}",
            task_dir=tmp_path / f"task-{index}",
            name=f"Toy {index}",
            category="misc",
            competition="demo",
            difficulty="1",
            prompt="Return the toy flag.",
            target_host=None,
            flag=f"flag{{toy-{index}}}",
            answer_format="flag{...}",
            metadata={},
            cybench_dir=tmp_path,
            task_list_path=tmp_path / "task_list.txt",
            has_service=False,
            has_compose=False,
        )
        for index in range(4)
    ]
    runner = CyBenchBaseline(
        agent="codex",
        output_dir=str(tmp_path / "out"),
        execution_mode="local",
        env_file="",
        skip_build=True,
        max_workers=3,
    )
    monkeypatch.setattr(runner, "_load_tasks", lambda: tasks)
    monkeypatch.setattr(runner, "_prepare_parallel_run", lambda: None)
    seen_threads: set[str] = set()

    def fake_run_one(task: cy_helpers.LoadedCybenchTask, output_dir: Path):
        seen_threads.add(threading.current_thread().name)
        time.sleep(0.02)
        output_dir.mkdir(parents=True)
        return {
            "benchmark": "cybench",
            "canonical_name": task.canonical_name,
            "solved": True,
            "run_status": "completed",
            "timed_out": False,
        }

    monkeypatch.setattr(runner, "_run_one", fake_run_one)

    report = runner.run()

    assert report["total"] == 4
    assert report["solved"] == 4
    assert report["max_workers"] == 3
    assert not (tmp_path / "out" / "results").exists()
    assert len(seen_threads) > 1


def test_nyuctf_baseline_run_uses_max_workers(tmp_path: Path, monkeypatch) -> None:
    challenges = [
        LoadedChallenge(
            canonical_name=f"toy-{index}",
            challenge_dir=tmp_path / f"challenge-{index}",
            name=f"Toy {index}",
            category="misc",
            description="desc",
            files=[],
            flag=f"flag{{toy-{index}}}",
            server_name=None,
            port=None,
            compose=False,
            dataset_path=tmp_path / "dataset.json",
        )
        for index in range(4)
    ]
    runner = NYUCTFBaseline(
        agent="codex",
        output_dir=str(tmp_path / "out"),
        execution_mode="local",
        env_file="",
        skip_build=True,
        max_workers=3,
    )
    monkeypatch.setattr(
        runner, "_load_challenges", lambda: (tmp_path / "dataset.json", challenges)
    )
    monkeypatch.setattr(runner, "_prepare_parallel_run", lambda: None)
    monkeypatch.setattr(
        runner,
        "evaluate",
        lambda: {"benchmark": "nyuctf", "total": len(challenges), "solved": 0},
    )
    seen_threads: set[str] = set()
    completed: list[str] = []

    def fake_run_one(challenge: LoadedChallenge, output_dir: Path):
        seen_threads.add(threading.current_thread().name)
        time.sleep(0.02)
        output_dir.mkdir(parents=True)
        completed.append(challenge.canonical_name)
        return {"solved": False}

    monkeypatch.setattr(runner, "_run_one", fake_run_one)

    report = runner.run()

    assert report["total"] == 4
    assert report["max_workers"] == 3
    assert sorted(completed) == [f"toy-{index}" for index in range(4)]
    assert len(seen_threads) > 1


def test_baseline_accepts_claude_code_agent_alias(tmp_path: Path) -> None:
    cybench = CyBenchBaseline(
        agent="claude-code",
        output_dir=str(tmp_path / "cybench"),
        execution_mode="local",
        env_file="",
    )
    nyuctf = NYUCTFBaseline(
        agent="claude-code",
        output_dir=str(tmp_path / "nyuctf"),
        execution_mode="local",
        env_file="",
    )

    assert cybench.provider == "claude"
    assert nyuctf.provider == "claude"
    assert cybench.model == "claude-opus-4-8"
    assert nyuctf.model == "claude-opus-4-8"
    assert cybench.image_tag == "opensage-cybench-claude:latest"
    assert nyuctf.image_tag == "opensage-nyuctf-claude:latest"


def test_baseline_images_are_agent_specific(tmp_path: Path, monkeypatch) -> None:
    cy_claude = CyBenchBaseline(
        agent="claude",
        output_dir=str(tmp_path / "cy-claude"),
    )
    cy_codex = CyBenchBaseline(
        agent="codex",
        output_dir=str(tmp_path / "cy-codex"),
    )
    nyu_claude = NYUCTFBaseline(
        agent="claude",
        output_dir=str(tmp_path / "nyu-claude"),
    )
    nyu_codex = NYUCTFBaseline(
        agent="codex",
        output_dir=str(tmp_path / "nyu-codex"),
    )

    assert cy_claude.image_tag == "opensage-cybench-claude:latest"
    assert cy_codex.image_tag == "opensage-cybench-codex:latest"
    assert nyu_claude.image_tag == "opensage-nyuctf-claude:latest"
    assert nyu_codex.image_tag == "opensage-nyuctf-codex:latest"
    assert str(cy_claude.env_file).endswith("benchmarks/cybench/claude-image/.env")
    assert cy_codex.env_file == ""
    assert str(nyu_claude.env_file).endswith("benchmarks/nyuctf/claude-image/.env")
    assert nyu_codex.env_file == ""

    build_commands: list[list[str]] = []
    monkeypatch.setattr(
        "benchmarks.cybench.baseline.subprocess.run",
        lambda cmd, check: build_commands.append(cmd),
    )
    cy_claude.build()
    cy_codex.build()

    assert build_commands[0][-1].endswith("benchmarks/cybench/claude-image")
    assert build_commands[1][-1].endswith("benchmarks/cybench/codex-image")


def test_codex_copies_provider_config_files(tmp_path: Path, monkeypatch) -> None:
    image_dir = tmp_path / "codex-image"
    image_dir.mkdir()
    (image_dir / "auth.json").write_text("{}")
    (image_dir / "config.toml").write_text("model = 'gpt-5.5'\n")
    runner = CyBenchBaseline(
        agent="codex",
        output_dir=str(tmp_path / "out"),
    )

    monkeypatch.setattr(
        "benchmarks.cybench.baseline._image_dir",
        lambda _provider: image_dir,
    )
    copy_commands: list[list[str]] = []
    monkeypatch.setattr(
        "benchmarks.cybench.baseline.subprocess.run",
        lambda cmd, check, **_: copy_commands.append(cmd),
    )

    create_cmd = runner._docker_create_command(
        container_name="codex-test",
        prompt_path=Path("/workspace/prompt.txt"),
        staged_dir=tmp_path / "shared",
        sandbox_dir=tmp_path / "workspace",
        network_name="ctfnet",
    )
    runner._copy_codex_config_to_container("codex-test")

    assert not any("/root/.codex/auth.json" in arg for arg in create_cmd)
    assert not any("/root/.codex/config.toml" in arg for arg in create_cmd)
    assert [
        "docker",
        "cp",
        str(image_dir / "auth.json"),
        "codex-test:/root/.codex/auth.json",
    ] in copy_commands
    assert [
        "docker",
        "cp",
        str(image_dir / "config.toml"),
        "codex-test:/root/.codex/config.toml",
    ] in copy_commands


def test_codex_image_creates_codex_home_and_initializes_mcp() -> None:
    for image_dir in (
        Path("benchmarks/cybench/codex-image"),
        Path("benchmarks/nyuctf/codex-image"),
    ):
        dockerfile = (image_dir / "Dockerfile").read_text()
        entrypoint = (image_dir / "entrypoint.sh").read_text()

        assert "RUN mkdir -p /root/.codex" in dockerfile
        assert "ghcr.io/opensage-agent/gdb_mcp:latest" in dockerfile
        assert "npm install -g @openai/codex" in dockerfile
        assert "mcp-remote" in dockerfile
        if image_dir == Path("benchmarks/nyuctf/codex-image"):
            assert "ARG CODEX_CLI_VERSION=0.124.0" in dockerfile
            assert "@openai/codex@${CODEX_CLI_VERSION}" in dockerfile
        assert "uv pip install mcp pygdbmi loguru" in dockerfile
        assert "pwndbg_2024.08.29_amd64.deb" in dockerfile
        assert 'mkdir -p "${CODEX_HOME:-/root/.codex}"' not in entrypoint
        assert "MCP_STARTUP_TIMEOUT" in entrypoint
        assert "wait_for_port gdb 127.0.0.1 1111 /root/gdb-mcp.log" in entrypoint
        assert (
            "wait_for_port ida-pro 127.0.0.1 ${IDA_MCP_PORT} /root/idalib-mcp.log"
            in entrypoint
        )
        assert (
            "wait_for_port pyghidra 127.0.0.1 ${PYGHIDRA_MCP_PORT} "
            "/root/pyghidra-mcp.log" in entrypoint
        )
        assert "uv run python -m gdb_mcp.gdb_mcp_server" in entrypoint
        assert "uv run idalib-mcp --host 127.0.0.1" in entrypoint
        assert "pyghidra-mcp" in entrypoint
        assert "-t streamable-http" in entrypoint
        assert "codex mcp add gdb -- mcp-remote http://localhost:1111/sse" in entrypoint
        assert (
            "codex mcp add ida-pro --url http://localhost:${IDA_MCP_PORT}/mcp"
            in entrypoint
        )
        assert (
            "codex mcp add pyghidra --url http://localhost:${PYGHIDRA_MCP_PORT}/mcp"
            in entrypoint
        )
        assert "codex mcp add ghidra" not in entrypoint


def test_nyuctf_claude_image_uses_pinned_npm_cli() -> None:
    dockerfile = Path("benchmarks/nyuctf/claude-image/Dockerfile").read_text()

    assert "curl -fsSL https://claude.ai/install.sh | bash" not in dockerfile
    assert "ARG CLAUDE_CODE_VERSION=2.1.154" in dockerfile
    assert (
        "npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" in dockerfile
    )


def test_codex_requires_provider_config_files(tmp_path: Path, monkeypatch) -> None:
    image_dir = tmp_path / "codex-image"
    image_dir.mkdir()
    runner = CyBenchBaseline(
        agent="codex",
        output_dir=str(tmp_path / "out"),
    )
    monkeypatch.setattr(
        "benchmarks.cybench.baseline._image_dir",
        lambda _provider: image_dir,
    )

    try:
        runner._codex_config_files()
    except FileNotFoundError as exc:
        assert "auth.json.template" in str(exc)
    else:
        raise AssertionError("expected missing Codex config to fail before Docker run")


def test_budget_tracker_records_token_estimate_and_exhaustion() -> None:
    tracker = CyBudgetTracker(
        budget_usd=0.00001,
        input_cost_per_million=1.0,
        output_cost_per_million=1.0,
    )

    tracker.observe_payload(
        {
            "usage": {
                "input_tokens": 10,
                "output_tokens": 10,
            }
        }
    )

    snapshot = tracker.snapshot()
    assert snapshot.is_estimate is True
    assert snapshot.source == "token_usage"
    assert snapshot.spent_usd > 0
    assert snapshot.budget_exhausted is True
    assert snapshot.exhausted_reason == "budget_exhausted"


def test_claude_stream_json_converts_to_session_trace(tmp_path: Path) -> None:
    stream = tmp_path / "claude_stream.jsonl"
    stream.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "I found the flag."},
                        {"type": "tool_use", "name": "Bash", "input": {"cmd": "ls"}},
                    ]
                },
            }
        )
        + "\n"
    )

    trace = cy_jsonl_to_session_trace(stream, provider="claude")

    parts = trace["events"][0]["content"]["parts"]
    assert parts[0]["text"] == "I found the flag."
    assert parts[1]["function_call"]["name"] == "Bash"


def test_cybench_baseline_simple_task_generates_score_and_trajectory(
    tmp_path: Path, monkeypatch
) -> None:
    relative = "benchmark/demo/ctf/crypto/toy"
    task_dir = tmp_path / relative
    (task_dir / "metadata").mkdir(parents=True)
    (task_dir / "metadata" / "metadata.json").write_text(
        json.dumps(
            {
                "categories": ["crypto"],
                "difficulty": "1",
                "hard_prompt": "Return the toy flag.",
                "target_host": "",
                "subtasks": [
                    {
                        "question": "flag?",
                        "answer": "flag{toy}",
                        "answer_format": "flag{...}",
                    }
                ],
            }
        )
    )
    (tmp_path / "task_list.txt").write_text(relative + "\n")

    def fake_invoke(self, *, sandbox_dir: Path, **_kwargs):
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        (sandbox_dir / "submission.json").write_text(
            json.dumps(
                {
                    "canonical_name": cy_helpers.safe_task_id(relative),
                    "flag": "flag{toy}",
                    "status": "solved",
                    "trajectory": "Read the prompt and returned the toy flag.",
                }
            )
        )
        (sandbox_dir / "final_flag.txt").write_text("flag{toy}\n")
        (sandbox_dir / "codex_events.jsonl").write_text(
            json.dumps({"type": "agent_message", "text": "Solved flag{toy}"}) + "\n"
        )
        return CyProcessResult(
            exit_code=0,
            exit_reason="finished",
            timed_out=False,
            budget_exhausted=False,
            budget=CyBudgetSnapshot(
                configured_budget_usd=100.0,
                spent_usd=0.01,
                source="test",
            ),
            started_at="2026-06-08T00:00:00+00:00",
            finished_at="2026-06-08T00:00:01+00:00",
            duration_seconds=1.0,
        )

    monkeypatch.setattr(CyBenchBaseline, "_invoke_agent", fake_invoke)
    monkeypatch.setattr(
        "benchmarks.cybench.baseline.helpers.ensure_docker_network", lambda _: None
    )
    monkeypatch.setattr(
        "benchmarks.cybench.helpers.judge_trajectory_sync",
        lambda **_: {"pass": True, "reason": "ok", "findings": []},
    )

    runner = CyBenchBaseline(
        agent="codex",
        bench_dir=str(tmp_path),
        output_dir=str(tmp_path / "out"),
        execution_mode="local",
        env_file="",
        skip_build=True,
        challenge_name=cy_helpers.safe_task_id(relative),
    )

    report = runner.run()

    task_output = tmp_path / "out" / "crypto_toy"
    score = json.loads((task_output / "score.json").read_text())
    config = json.loads((task_output / "metadata.json").read_text())
    budget = json.loads((task_output / "cost_info.json").read_text())

    assert report["solved"] == 1
    assert score["solved"] is True
    assert score["groundtruth_flag"] == "flag{toy}"
    assert score["reported_flag"] == "flag{toy}"
    assert score["exit_reason"] == "solved"
    assert config["driver"] == "codex-baseline"
    assert config["model"] == "gpt-5.5"
    assert config["reasoning_effort"] == "high"
    assert budget["spent_usd"] == 0.01
    assert budget["configured_budget_usd"] == 100.0
    assert not (task_output / "submission_trajectory").exists()
    assert not (task_output / "run_metadata.json").exists()
    # Container output is consolidated under raw/.
    assert (task_output / "raw" / "final_flag.txt").exists()
    assert not (task_output / "sandbox_output").exists()


def test_nyuctf_baseline_adds_submission_trajectory_to_trace(tmp_path: Path) -> None:
    runner = NYUCTFBaseline(
        agent="codex",
        output_dir=str(tmp_path / "out"),
        execution_mode="local",
        env_file="",
    )
    sandbox = tmp_path / "sandbox"
    workspace = sandbox / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "submission.json").write_text(
        json.dumps(
            {
                "canonical_name": "toy",
                "flag": None,
                "status": "unsolved",
                "trajectory": "tried the service and stopped",
            }
        )
    )
    challenge = LoadedChallenge(
        split="test",
        canonical_name="toy",
        challenge_dir=tmp_path,
        name="Toy",
        category="misc",
        description="desc",
        files=[],
        flag="flag{toy}",
        server_name=None,
        port=None,
        compose=False,
        dataset_path=tmp_path / "dataset.json",
    )

    trace = runner._augment_trace_with_submission(
        {"events": []},
        sandbox_dir=sandbox,
        challenge=challenge,
    )

    assert trace["events"][0]["author"] == "solver_report"
    assert "tried the service" in trace["events"][0]["content"]["parts"][0]["text"]


def test_nyuctf_baseline_keeps_runner_started_service_running(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = NYUCTFBaseline(
        agent="codex",
        output_dir=str(tmp_path / "out"),
        execution_mode="local",
        env_file="",
    )
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    challenge = LoadedChallenge(
        split="test",
        canonical_name="toy",
        challenge_dir=challenge_dir,
        name="Toy",
        category="misc",
        description="desc",
        files=[],
        flag="flag{toy}",
        server_name=None,
        port=None,
        compose=True,
        dataset_path=tmp_path / "dataset.json",
    )
    monkeypatch.setattr(runner, "_ensure_network", lambda **_: None)
    monkeypatch.setattr(runner, "_start_challenge", lambda *_, **__: True)
    monkeypatch.setattr(
        runner,
        "_stop_challenge",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("runner-started services should remain running")
        ),
    )
    monkeypatch.setattr(
        runner,
        "_invoke_agent",
        lambda **_: NYUProcessResult(
            exit_code=0,
            exit_reason="finished",
            timed_out=False,
            budget_exhausted=False,
            budget=NYUBudgetSnapshot(
                configured_budget_usd=100.0,
                spent_usd=0.0,
                source="test",
            ),
            started_at="2026-06-08T00:00:00+00:00",
            finished_at="2026-06-08T00:00:01+00:00",
            duration_seconds=1.0,
        ),
    )
    monkeypatch.setattr(
        runner,
        "_score",
        lambda output_dir, challenge, prompt, result: {"solved": False},
    )

    score = runner._run_one(challenge, tmp_path / "out" / "toy")

    assert score == {"solved": False}
