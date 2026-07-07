from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

from benchmarks.cybench import helpers
from benchmarks.cybench.sagectf import CyBench
from opensage.evaluation.base import Evaluation, EvaluationTask
from opensage.llm.budget import BudgetManager


def _write_task(
    root: Path,
    relative: str = "benchmark/hackthebox/cyber-apocalypse-2024/crypto/[Very Easy] Dynastic",
    *,
    target_host: str = "",
    flag: str = "HTB{correct}",
) -> helpers.LoadedCybenchTask:
    task_dir = root / relative
    (task_dir / "metadata").mkdir(parents=True)
    metadata = {
        "categories": ["crypto"],
        "difficulty": "1",
        "easy_prompt": "easy prompt",
        "hard_prompt": "hard prompt",
        "target_host": target_host,
        "subtasks": [
            {
                "question": "What's the flag?",
                "answer": flag,
                "answer_format": "HTB{*******}",
            }
        ],
    }
    (task_dir / "metadata" / "metadata.json").write_text(json.dumps(metadata))
    (root / "task_list.txt").write_text(relative + "\n")
    return helpers.LoadedCybenchTask(
        canonical_name=helpers.safe_task_id(relative),
        relative_task_dir=relative,
        task_dir=task_dir,
        name=task_dir.name,
        category="crypto",
        competition="hackthebox/cyber-apocalypse-2024",
        difficulty="1",
        prompt="hard prompt",
        target_host=target_host or None,
        flag=flag,
        answer_format="HTB{*******}",
        metadata=metadata,
        cybench_dir=root,
        task_list_path=root / "task_list.txt",
        has_service=bool(target_host),
        has_compose=False,
    )


def test_load_cybench_tasks_reads_task_list_and_metadata(tmp_path: Path) -> None:
    task = _write_task(tmp_path)

    task_list_path, tasks = helpers.load_cybench_tasks(cybench_dir=tmp_path)

    assert task_list_path == (tmp_path / "task_list.txt").resolve()
    assert len(tasks) == 1
    assert tasks[0].canonical_name == task.canonical_name
    assert tasks[0].flag == "HTB{correct}"
    assert tasks[0].prompt == "hard prompt"


def test_task_output_dir_name_uses_category_and_challenge_without_difficulty(
    tmp_path: Path,
) -> None:
    crypto_task = _write_task(tmp_path)
    web_task = _write_task(
        tmp_path / "webroot",
        relative="benchmark/hackthebox/cyber-apocalypse-2024/web/[Very Easy] Flag Command",
        target_host="web_flag:1337",
    )
    web_task.category = "web"

    assert helpers.task_output_dir_name(crypto_task) == "crypto_Dynastic"
    assert helpers.task_output_dir_name(web_task) == "web_Flag__Command"


def test_stage_cybench_task_uses_init_script_without_solution_metadata(
    tmp_path: Path,
) -> None:
    task = _write_task(tmp_path)
    (task.task_dir / "init_script.sh").write_text(
        "#!/bin/bash\n"
        "set -e\n"
        'mkdir -p "$1"\n'
        'printf public > "$1/output.txt"\n'
        "printf 'generated prompt for %s' \"$2\"\n"
    )

    staged = tmp_path / "staged"
    prompt = helpers.stage_cybench_task(task, staged, easy_prompt=True)

    assert prompt == "generated prompt for true"
    assert (staged / "output.txt").read_text() == "public"
    manifest = json.loads((staged / "cybench_task_manifest.json").read_text())
    assert manifest["files"] == ["output.txt"]
    assert "metadata/solution" not in json.dumps(manifest)


def test_stage_cybench_task_ignores_metadata_requirements_script(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task = _write_task(tmp_path)
    (task.task_dir / "metadata" / "requirements.sh").write_text(
        "#!/bin/bash\napt-get update\n"
    )
    (task.task_dir / "init_script.sh").write_text(
        "#!/bin/bash\n"
        "set -e\n"
        'mkdir -p "$1"\n'
        'printf public > "$1/output.txt"\n'
        "printf prompt\n"
    )
    original_run_command = helpers.run_command

    def fake_run_command(cmd, **kwargs):
        if cmd[-1].endswith("requirements.sh"):
            raise AssertionError("metadata/requirements.sh should not run")
        return original_run_command(cmd, **kwargs)

    monkeypatch.setattr(helpers, "run_command", fake_run_command)

    staged = tmp_path / "staged"
    prompt = helpers.stage_cybench_task(task, staged)

    assert prompt == "prompt"
    assert (staged / "output.txt").read_text() == "public"


def test_make_cybench_compose_file_attaches_ctfnet_and_removes_ports(
    tmp_path: Path,
) -> None:
    task = _write_task(
        tmp_path,
        relative="benchmark/hackthebox/cyber-apocalypse-2024/web/[Very Easy] Flag Command",
        target_host="web_flag:1337",
    )
    task.has_compose = True
    (task.task_dir / "docker-compose.yml").write_text(
        "services:\n"
        "  web_flag:\n"
        "    build: ./challenge\n"
        "    ports:\n"
        "      - '1337:1337'\n"
        "networks:\n"
        "  shared_net:\n"
        "    external: true\n"
    )

    compose_path = helpers.make_cybench_compose_file(
        task=task,
        output_dir=tmp_path / "compose",
        network_name="ctfnet",
        remove_host_ports=True,
    )
    data = helpers.yaml.safe_load(compose_path.read_text())

    service = data["services"]["web_flag"]
    assert "ports" not in service
    assert service["build"] == str((task.task_dir / "challenge").resolve())
    assert service["networks"]["ctfnet"]["aliases"] == ["web_flag"]
    assert data["networks"]["ctfnet"] == {"external": True}
    assert data["networks"]["shared_net"] == {}


def test_connect_existing_init_script_container_for_non_compose_service(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task = _write_task(
        tmp_path,
        relative="benchmark/project-sekai-ctf/sekaictf-2023/crypto/cryptography-2",
        target_host="cryptography2:9999",
    )
    (task.task_dir / "init_script.sh").write_text(
        "docker run -d -p 9999:9999 --name cryptography2-instance cryptography2-challenge\n"
    )
    calls = []

    def fake_run_command(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["docker", "network", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["docker", "container", "inspect"]:
            return SimpleNamespace(
                returncode=0 if cmd[-1] == "cryptography2-instance" else 1,
                stdout="",
                stderr="",
            )
        if cmd[:3] == ["docker", "network", "connect"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(helpers, "run_command", fake_run_command)
    monkeypatch.setattr(helpers, "wait_for_target_host", lambda *_, **__: True)

    started = helpers.start_cybench_service(
        task,
        network_name="ctfnet",
        compose_output_dir=tmp_path / "compose",
    )

    assert started is not None
    assert started.compose_file is None
    assert [
        "docker",
        "network",
        "connect",
        "--alias",
        "cryptography2",
        "ctfnet",
        "cryptography2-instance",
    ] in calls


def test_start_cybench_compose_service_reuses_existing_images_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task = _write_task(
        tmp_path,
        relative="benchmark/hackthebox/cyber-apocalypse-2024/web/[Very Easy] Flag Command",
        target_host="web_flag:1337",
    )
    task.has_compose = True
    (task.task_dir / "docker-compose.yml").write_text(
        "services:\n  web_flag:\n    build: ./challenge\n"
    )
    calls = []

    def fake_run_command(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["docker", "network", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["docker", "compose", "-p"] and "ps" in cmd:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["docker", "compose", "-p"] and "up" in cmd:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(helpers, "run_command", fake_run_command)
    monkeypatch.setattr(helpers, "wait_for_target_host", lambda *_, **__: True)

    started = helpers.start_cybench_service(
        task,
        network_name="shared_net",
        compose_output_dir=tmp_path / "compose",
    )

    assert started is not None
    compose_calls = [call for call in calls if call[:3] == ["docker", "compose", "-p"]]
    up_calls = [call for call in compose_calls if "up" in call]
    assert len(up_calls) == 1
    assert "--build" not in up_calls[0]


def test_start_cybench_compose_service_keeps_existing_service_running(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task = _write_task(
        tmp_path,
        relative="benchmark/hackthebox/cyber-apocalypse-2024/web/[Very Easy] Flag Command",
        target_host="web_flag:1337",
    )
    task.has_compose = True
    (task.task_dir / "docker-compose.yml").write_text(
        "services:\n  web_flag:\n    build: ./challenge\n"
    )
    calls = []

    def fake_run_command(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["docker", "network", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["docker", "compose", "-p"] and "ps" in cmd:
            return SimpleNamespace(
                returncode=0, stdout="existing-container\n", stderr=""
            )
        if cmd[:3] == ["docker", "compose", "-p"] and "up" in cmd:
            raise AssertionError("existing service should not be recreated")
        if cmd[:3] == ["docker", "compose", "-p"] and "down" in cmd:
            raise AssertionError("existing service should not be stopped")
        raise AssertionError(cmd)

    monkeypatch.setattr(helpers, "run_command", fake_run_command)

    started = helpers.start_cybench_service(
        task,
        network_name="shared_net",
        compose_output_dir=tmp_path / "compose",
    )

    assert started is not None
    assert started.started_by_runner is False


def test_start_cybench_compose_service_can_force_image_rebuild(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task = _write_task(
        tmp_path,
        relative="benchmark/hackthebox/cyber-apocalypse-2024/web/[Very Easy] Flag Command",
        target_host="web_flag:1337",
    )
    task.has_compose = True
    (task.task_dir / "docker-compose.yml").write_text(
        "services:\n  web_flag:\n    build: ./challenge\n"
    )
    calls = []

    def fake_run_command(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["docker", "network", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["docker", "compose", "-p"] and "ps" in cmd:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["docker", "compose", "-p"] and "up" in cmd:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(helpers, "run_command", fake_run_command)
    monkeypatch.setattr(helpers, "wait_for_target_host", lambda *_, **__: True)

    helpers.start_cybench_service(
        task,
        network_name="shared_net",
        compose_output_dir=tmp_path / "compose",
        rebuild_images=True,
    )

    compose_calls = [call for call in calls if call[:3] == ["docker", "compose", "-p"]]
    assert len(compose_calls) == 1
    assert "--build" in compose_calls[0]


def test_score_task_writes_score_without_judge_on_unsolved(
    tmp_path: Path,
) -> None:
    task = _write_task(tmp_path)
    output_dir = tmp_path / "out" / task.canonical_name
    workspace = output_dir / "raw"
    workspace.mkdir(parents=True)
    (workspace / "submission.json").write_text(
        json.dumps(
            {
                "canonical_name": task.canonical_name,
                "flag": None,
                "trajectory": "looked at files",
                "status": "unsolved",
            }
        )
    )

    score = helpers.score_task(
        output_dir=output_dir,
        task=task,
        run_info={
            "exit_reason": "finished",
            "started_at": "2026-06-05T00:00:00+00:00",
            "finished_at": "2026-06-05T00:00:01+00:00",
            "duration_seconds": 1.0,
            "time_limit_seconds": 60,
        },
    )

    assert score["solved"] is False
    assert score["groundtruth_flag"] == task.flag
    assert score["exit_reason"] == "not_solved"
    assert not (output_dir / "submission_trajectory").exists()
    assert helpers.is_completed_task_output(output_dir, task) is True


def test_run_artifacts_written_at_top_level(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir(parents=True)
    (output_dir / "config_used.toml").write_text("# config\n")

    helpers.write_run_artifacts(
        output_dir=output_dir,
        prompt="prompt",
        session_trace={"events": []},
    )

    assert (output_dir / "session_trace.json").exists()
    assert (output_dir / "prompt.txt").exists()
    assert (output_dir / "config_used.toml").exists()
    # Container output lives only under raw/ (written elsewhere); no workspace/ archive.
    assert not (output_dir / "workspace").exists()
    assert not (output_dir / "run_metadata.json").exists()


def test_evaluate_marks_existing_outputs_as_skipped(tmp_path: Path) -> None:
    task = _write_task(tmp_path)
    output_root = tmp_path / "eval"
    task_output = output_root / helpers.task_output_dir_name(task)
    task_output.mkdir(parents=True)
    (task_output / "score.json").write_text(
        json.dumps(
            {
                "canonical_name": task.canonical_name,
                "difficulty": task.difficulty,
                "solved": False,
                "reported_flag": None,
                "groundtruth_flag": task.flag,
                "exit_reason": "not_solved",
                "judge_pass": False,
                "judge_findings": [],
                "started_at": None,
                "finished_at": None,
                "duration_seconds": None,
                "time_limit_seconds": 60,
                "budget_usd": 100.0,
            }
        )
    )

    bench = object.__new__(CyBench)
    bench.output_dir = str(output_root)
    bench.agent_dir = str(tmp_path / "sagectf" / "ctf_agent")
    bench._cybench_dir_path = tmp_path
    bench._task_list_path = tmp_path / "task_list.txt"
    bench._selected_tasks_cache = [task]
    bench._run_time_limit_seconds = 60
    bench.budget = 100.0
    bench._run_started_at = "2026-06-05T00:00:00+00:00"
    bench.challenge_name = None
    bench.max_challenges = None

    summary = bench.evaluate()

    assert summary["skipped_reused"] == 1
    assert not (output_root / "results").exists()


def test_live_workspace_mount_replaces_existing_workspace_bind(tmp_path: Path) -> None:
    bench = object.__new__(CyBench)
    task = SimpleNamespace(output_dir=str(tmp_path))
    main_config = SimpleNamespace(volumes=["/old:/workspace:rw", "/keep:/keep:ro"])
    opensage_session = SimpleNamespace(
        config=SimpleNamespace(
            sandbox=SimpleNamespace(
                backend="native",
                sandboxes={"main": main_config},
            )
        )
    )

    bench._ensure_host_workspace_mount(task, opensage_session)

    assert (
        f"{tmp_path.resolve() / 'workspace_live'}:/workspace:rw" in main_config.volumes
    )
    assert "/old:/workspace:rw" not in main_config.volumes
    assert "/keep:/keep:ro" in main_config.volumes


def test_per_challenge_budget_updates_session_config_and_manager() -> None:
    bench = object.__new__(CyBench)
    bench.budget = 100.0
    opensage_session = SimpleNamespace(
        config=SimpleNamespace(model=SimpleNamespace(budget=0.0)),
        budget=BudgetManager(configured_budget=0.0),
    )

    bench._apply_per_challenge_budget(opensage_session)

    assert opensage_session.config.model.budget == 100.0
    assert opensage_session.budget.configured_budget == 100.0
    assert opensage_session.budget.budget_exhausted is False


def test_config_template_variables_reuse_agent_task_name_by_default(
    tmp_path: Path,
) -> None:
    bench = object.__new__(CyBench)
    bench.reuse_sandbox_images = True
    task = EvaluationTask(
        id="hackthebox__cyber-apocalypse-2024__crypto__Very__Easy__Dynastic",
        sample={},
        first_user_message="prompt",
        output_dir=str(tmp_path),
        initial_data_dir=str(tmp_path / "shared"),
    )

    variables = bench._get_config_template_variables(task)

    assert "TASK_NAME" not in variables
    assert variables["ABSOLUTE_SHARED_DATA_PATH"] == str(
        (tmp_path / "shared").resolve()
    )


def test_config_template_variables_can_use_per_task_image_name(
    tmp_path: Path,
) -> None:
    bench = object.__new__(CyBench)
    bench.reuse_sandbox_images = False
    task = EvaluationTask(
        id="hackthebox__cyber-apocalypse-2024__crypto__Very__Easy__Dynastic",
        sample={},
        first_user_message="prompt",
        output_dir=str(tmp_path),
        initial_data_dir=str(tmp_path / "shared"),
    )

    variables = bench._get_config_template_variables(task)

    assert (
        variables["TASK_NAME"]
        == "hackthebox__cyber-apocalypse-2024__crypto__very__easy__dynastic"
    )
    assert variables["ABSOLUTE_SHARED_DATA_PATH"] == str(
        (tmp_path / "shared").resolve()
    )


def test_docker_safe_name_strips_boundary_after_truncation() -> None:
    name = helpers.docker_safe_name(
        "hackthebox__cyber-apocalypse-2024__web__Very__Easy__Flag__Command",
        max_length=52,
    )

    assert name == "hackthebox__cyber-apocalypse-2024__web__very__easy"
    assert name == name.lower()
    assert name[-1].isalnum()


def test_create_task_force_rerun_uses_permission_aware_reset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cybench_task = _write_task(tmp_path)
    output_root = tmp_path / "out"
    output_dir = output_root / helpers.task_output_dir_name(cybench_task)
    output_dir.mkdir(parents=True)
    calls = []

    def fake_reset(path: Path) -> None:
        calls.append(path)
        output_dir.rmdir()

    monkeypatch.setattr(
        "benchmarks.cybench.sagectf.reset_output_path_for_rerun",
        fake_reset,
    )
    bench = object.__new__(CyBench)
    bench.output_dir = str(output_root)
    bench.force_rerun = True
    bench.network_name = "ctfnet"

    task = bench._create_task(cybench_task.to_sample())

    assert calls == [output_dir]
    assert task.id == cybench_task.canonical_name
    assert Path(task.output_dir).name == "crypto_Dynastic"


def test_cybench_fake_user_stops_after_output_contract(tmp_path: Path) -> None:
    bench = object.__new__(CyBench)
    bench._active_task_for_fake_user = None
    bench.run_until_explicit_finish = True
    bench.continuation_prompt = "continue"
    task = EvaluationTask(
        id="task-id",
        sample={},
        first_user_message="prompt",
        output_dir=str(tmp_path / "out"),
    )
    workspace = Path(task.output_dir) / "workspace_live"
    workspace.mkdir(parents=True)
    (workspace / "final_flag.txt").write_text("HTB{flag}\n")
    (workspace / "submission.json").write_text(
        json.dumps(
            {"canonical_name": "task-id", "flag": "HTB{flag}", "status": "solved"}
        )
    )
    bench._active_task_for_fake_user = task
    fake_user = bench._get_fake_user_fn()
    session = SimpleNamespace(state={})

    response = asyncio.run(fake_user(session))

    assert response is None
    assert session.state["task_finished"] is True


def test_create_task_defers_cybench_staging_until_run(tmp_path: Path) -> None:
    cybench_task = _write_task(tmp_path)
    (cybench_task.task_dir / "init_script.sh").write_text(
        "#!/bin/bash\n"
        "set -e\n"
        'mkdir -p "$1"\n'
        'printf public > "$1/output.txt"\n'
        "printf generated-prompt\n"
    )

    bench = object.__new__(CyBench)
    bench.output_dir = str(tmp_path / "out")
    bench.force_rerun = False
    bench.network_name = "ctfnet"
    bench.easy_prompt = False
    bench.stage_script_timeout = 60

    task = bench._create_task(cybench_task.to_sample())
    staged_dir = Path(task.initial_data_dir)

    assert task.sample["_generated_prompt"] == ""
    assert "hard prompt" in task.first_user_message
    assert "generated-prompt" not in task.first_user_message
    assert not (staged_dir / "output.txt").exists()

    generated_prompt = bench._stage_task_for_run(
        cybench_task,
        task,
        deadline=time.time() + 60,
    )

    assert generated_prompt == "generated-prompt"
    assert (staged_dir / "output.txt").read_text() == "public"


def test_generate_one_timeout_writes_score(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cybench_task = _write_task(tmp_path, flag="HTB{correct}")
    output_dir = tmp_path / "out" / cybench_task.canonical_name
    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()

    bench = object.__new__(CyBench)
    bench.time_limit = "1s"
    bench._run_time_limit_seconds = 1
    bench.budget = 100.0
    bench.network_name = "ctfnet"
    bench.remove_host_ports = True
    bench.skip_services = False
    bench.easy_prompt = False
    bench.stage_script_timeout = 60
    bench.agent_dir = str(tmp_path / "sagectf" / "ctf_agent")
    bench._before_generate_one_callback = lambda task: None
    bench._register_opensage_session = lambda task: None

    async def _prepare_environment(task):
        return None

    async def _run_agent(self, task, agent):
        workspace = Path(task.output_dir) / "workspace_live"
        workspace.mkdir(parents=True)
        (workspace / "submission.json").write_text(
            json.dumps(
                {
                    "canonical_name": cybench_task.canonical_name,
                    "flag": None,
                    "trajectory": "timed out after initial exploration",
                    "status": "unsolved",
                }
            )
        )
        await asyncio.sleep(2)

    async def _base_collect_outputs(self, task, session):
        return {"session": None}

    async def _recover_session(task):
        return None

    bench._prepare_environment = _prepare_environment
    bench._prepare_agent = lambda task: object()
    bench._recover_session = _recover_session
    bench._persist_session_snapshot = lambda task: None
    bench._cleanup_opensage_session = lambda task: None
    bench._cleanup_live_workspace_if_archived = lambda task: None
    fake_session = SimpleNamespace(
        config=SimpleNamespace(
            save_to_toml=lambda path: Path(path).write_text("# fake config\n")
        )
    )
    monkeypatch.setattr(
        EvaluationTask,
        "opensage_session",
        property(lambda self: fake_session),
    )
    monkeypatch.setattr(helpers, "start_cybench_service", lambda *_, **__: None)
    monkeypatch.setattr(
        "benchmarks.cybench.sagectf.start_cybench_service", lambda *_, **__: None
    )
    monkeypatch.setattr(
        "benchmarks.cybench.sagectf.ensure_docker_network", lambda _: None
    )
    monkeypatch.setattr(Evaluation, "_run_agent", _run_agent)
    monkeypatch.setattr(Evaluation, "_collect_outputs", _base_collect_outputs)

    task = EvaluationTask(
        id=cybench_task.canonical_name,
        sample=cybench_task.to_sample(),
        first_user_message="solve this task",
        output_dir=str(output_dir),
        initial_data_dir=str(staged_dir),
        export_dir_in_sandbox="/workspace",
    )

    try:
        asyncio.run(bench._generate_one(task))
    except TimeoutError:
        pass
    else:
        raise AssertionError("expected agent timeout")

    score = json.loads((output_dir / "score.json").read_text())

    assert score["exit_reason"] == "timeout"
    assert score["budget_usd"] == 100.0
    assert not (output_dir / "run_metadata.json").exists()
    assert not (output_dir / "submission_trajectory").exists()
    assert (output_dir / "raw" / "submission.json").exists()
    assert (output_dir / "error.json").exists()


def test_generate_one_staging_timeout_writes_score(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cybench_task = _write_task(tmp_path, flag="HTB{correct}")
    output_dir = tmp_path / "out" / cybench_task.canonical_name
    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()

    bench = object.__new__(CyBench)
    bench.time_limit = "1s"
    bench._run_time_limit_seconds = 1
    bench.budget = 100.0
    bench.network_name = "ctfnet"
    bench.remove_host_ports = True
    bench.skip_services = False
    bench.easy_prompt = False
    bench.stage_script_timeout = 60
    bench.agent_dir = str(tmp_path / "sagectf" / "ctf_agent")
    bench._persist_session_snapshot = lambda task: None
    bench._cleanup_opensage_session = lambda task: None
    bench._cleanup_live_workspace_if_archived = lambda task: None

    async def _base_collect_outputs(self, task, session):
        return {"session": None}

    def _stage_timeout(*args, **kwargs):
        raise TimeoutError("staging exceeded the per-task limit")

    async def _recover_session(task):
        return None

    bench._recover_session = _recover_session
    monkeypatch.setattr(
        "benchmarks.cybench.sagectf.stage_cybench_task",
        _stage_timeout,
    )
    monkeypatch.setattr(
        "benchmarks.cybench.sagectf.ensure_docker_network", lambda _: None
    )
    monkeypatch.setattr(Evaluation, "_collect_outputs", _base_collect_outputs)

    task = EvaluationTask(
        id=cybench_task.canonical_name,
        sample=cybench_task.to_sample(),
        first_user_message="solve this task",
        output_dir=str(output_dir),
        initial_data_dir=str(staged_dir),
        export_dir_in_sandbox="/workspace",
    )

    try:
        asyncio.run(bench._generate_one(task))
    except TimeoutError:
        pass
    else:
        raise AssertionError("expected staging timeout")

    score = json.loads((output_dir / "score.json").read_text())

    assert score["exit_reason"] == "timeout"
    assert score["budget_usd"] == 100.0
    assert not (output_dir / "run_metadata.json").exists()
    assert not (output_dir / "submission_trajectory").exists()
    assert not (output_dir / "raw").exists()
