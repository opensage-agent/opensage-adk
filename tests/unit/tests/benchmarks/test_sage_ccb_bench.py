from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]
SAGE_CCB_DIR = ROOT / "benchmarks" / "sage-ccb"

if str(SAGE_CCB_DIR) not in sys.path:
    sys.path.insert(0, str(SAGE_CCB_DIR))

_SPEC = importlib.util.spec_from_file_location(
    "sage_ccb_run_under_test", SAGE_CCB_DIR / "run.py"
)
assert _SPEC and _SPEC.loader
sage_run = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = sage_run
_SPEC.loader.exec_module(sage_run)
sage_helpers = sys.modules["helpers"]


def _loaded_challenge(tmp_path: Path):
    challenge_dir = tmp_path / "challenge"
    challenge_dir.mkdir()
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text("{}")
    return sage_helpers.LoadedChallenge(
        split="sage-ccb",
        canonical_name="2026-rev-sun_temple",
        challenge_dir=challenge_dir,
        name="Sun Temple",
        category="rev",
        description="Reverse the binary.",
        files=[],
        flag="gigem{correct}",
        server_name=None,
        port=None,
        compose=False,
        dataset_path=dataset_path,
    )


def test_live_workspace_mount_replaces_existing_workspace_bind(tmp_path: Path) -> None:
    bench = object.__new__(sage_run.SAGE_CCB_Bench)
    task = sage_run.EvaluationTask(
        id="sage-ccb",
        sample={},
        first_user_message="prompt",
        output_dir=str(tmp_path),
        export_dir_in_sandbox="/workspace",
    )
    main_config = SimpleNamespace(
        volumes=["/old/workspace:/workspace:rw", "/keep:/keep:ro"]
    )
    opensage_session = SimpleNamespace(
        config=SimpleNamespace(
            sandbox=SimpleNamespace(
                backend="native",
                sandboxes={"main": main_config},
            )
        )
    )

    bench._ensure_host_workspace_mount(task, opensage_session)

    live_mount = f"{tmp_path.resolve() / 'workspace_live'}:/workspace:rw"
    assert live_mount in main_config.volumes
    assert "/old/workspace:/workspace:rw" not in main_config.volumes
    assert "/keep:/keep:ro" in main_config.volumes


def test_generate_one_scores_partial_workspace_after_agent_crash(
    tmp_path: Path, monkeypatch
) -> None:
    bench = object.__new__(sage_run.SAGE_CCB_Bench)
    bench.timeout = "1h"
    bench.network_name = "ctfnet"
    bench.remove_host_ports = True
    challenge = _loaded_challenge(tmp_path)
    bench._sample_challenges = lambda sample: [challenge]
    bench._before_generate_one_callback = lambda task: None
    bench._register_opensage_session = lambda task: bench._host_workspace_dir(
        task
    ).mkdir(parents=True, exist_ok=True)

    async def _prepare_environment(task):
        return None

    async def _run_agent(task, agent):
        workspace = bench._host_workspace_dir(task)
        submissions = workspace / "submissions"
        submissions.mkdir(parents=True)
        (submissions / f"{challenge.canonical_name}.json").write_text(
            json.dumps(
                {
                    "canonical_name": challenge.canonical_name,
                    "flag": challenge.flag,
                    "trajectory": "I reversed the staged binary and extracted the flag.",
                    "status": "solved",
                }
            )
        )
        (workspace / "completed.json").write_text(
            json.dumps({"finished": False, "solved": [challenge.canonical_name]})
        )
        raise RuntimeError("sandbox crashed")

    async def _base_collect_outputs(self, task, session):
        raise RuntimeError("container unavailable")

    async def _recover_session(task):
        return None

    bench._prepare_environment = _prepare_environment
    bench._prepare_agent = lambda task: object()
    bench._run_agent = _run_agent
    bench._recover_session = _recover_session
    bench._cleanup_opensage_session = lambda task: None
    monkeypatch.setattr(sage_run, "start_challenge_services", lambda *_, **__: [])
    monkeypatch.setattr(sage_run, "stop_challenge_services", lambda _: None)
    monkeypatch.setattr(sage_run.Evaluation, "_collect_outputs", _base_collect_outputs)

    task = SimpleNamespace(
        id="sage-ccb",
        sample={"challenges": []},
        first_user_message="prompt",
        output_dir=str(tmp_path),
        initial_data_dir=None,
        export_dir_in_sandbox="/workspace",
        session_id="test-session",
        opensage_session=SimpleNamespace(
            config=SimpleNamespace(save_to_toml=lambda path: Path(path).write_text(""))
        ),
    )

    with pytest.raises(RuntimeError, match="sandbox crashed"):
        asyncio.run(bench._generate_one(task))

    raw_submission = (
        tmp_path
        / "raw"
        / "workspace"
        / "submissions"
        / f"{challenge.canonical_name}.json"
    )
    assert raw_submission.exists()
    assert not (tmp_path / "workspace_live").exists()

    metadata = json.loads((tmp_path / "raw" / "run_metadata.json").read_text())
    assert metadata["suite_exit_reason"] == "task_error"
    assert metadata["error"]["type"] == "RuntimeError"

    monkeypatch.setattr(
        sage_helpers,
        "judge_sage_trajectory_sync",
        lambda **_: {"pass": True, "reason": "ok", "findings": []},
    )
    summary = sage_helpers.score_and_write_results(
        output_dir=tmp_path,
        challenges=[challenge],
        judge_model="judge-model",
    )

    assert summary["solved"] == 1
    result_payload = json.loads((tmp_path / "results" / "results.json").read_text())
    assert result_payload["challenges"][0]["solved"] is True


def test_generate_one_preserves_early_setup_error(tmp_path: Path, monkeypatch) -> None:
    bench = object.__new__(sage_run.SAGE_CCB_Bench)
    bench.timeout = "1h"
    bench.network_name = "ctfnet"
    bench.remove_host_ports = True
    challenge = _loaded_challenge(tmp_path)
    bench._sample_challenges = lambda sample: [challenge]
    bench._before_generate_one_callback = lambda task: None

    def _register_opensage_session(task):
        bench._host_workspace_dir(task).mkdir(parents=True, exist_ok=True)
        raise RuntimeError("setup failed")

    async def _recover_session(task):
        return None

    bench._register_opensage_session = _register_opensage_session
    bench._recover_session = _recover_session
    bench._cleanup_opensage_session = lambda task: None
    monkeypatch.setattr(sage_run, "start_challenge_services", lambda *_, **__: [])
    monkeypatch.setattr(sage_run, "stop_challenge_services", lambda _: None)

    task = SimpleNamespace(
        id="sage-ccb",
        sample={"challenges": []},
        first_user_message="prompt",
        output_dir=str(tmp_path),
        initial_data_dir=None,
        export_dir_in_sandbox="/workspace",
        session_id="test-session",
        opensage_session=None,
    )

    with pytest.raises(RuntimeError, match="setup failed"):
        asyncio.run(bench._generate_one(task))

    metadata = json.loads((tmp_path / "raw" / "run_metadata.json").read_text())
    assert metadata["suite_exit_reason"] == "task_error"
    assert metadata["error"]["type"] == "RuntimeError"
    assert metadata["error"]["message"] == "setup failed"
