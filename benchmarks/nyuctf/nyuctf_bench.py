from __future__ import annotations

import datetime
import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from random import shuffle
from typing import Any

import datasets
import fire
from google.adk.sessions import Session

from opensage import get_opensage_session
from opensage.evaluation.base import Evaluation, EvaluationTask
from opensage.utils.project_info import PROJECT_PATH

from .helpers import (
    LoadedChallenge,
    build_challenge_prompt,
    find_exact_flag,
    judge_trajectory_sync,
    load_dataset,
    make_transcript_payload,
    run_command,
)

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class NYU_CTF_Bench(Evaluation):
    """OpenSage evaluation adapter for NYU_CTF_Bench."""

    dataset_path: str = ""
    agent_dir: str = str(PROJECT_PATH / "examples" / "agents" / "ctf_agent")
    config_template_path: str = str(
        PROJECT_PATH / "examples" / "agents" / "ctf_agent" / "config.toml"
    )

    name: str = "nyuctf"
    max_workers: int = 1
    max_llm_calls: int = 150
    run_until_explicit_finish: bool = False

    split: str = "test"
    challenge_name: str | None = None
    dataset_json: str | None = None
    repository_dir: str | None = None
    max_challenges: int | None = None
    network_name: str = "ctfnet"

    submission_agent: str = "opensage-ctf"
    submission_model: str = "claude-opus-4-6"
    submission_link: str = "https://www.opensage-agent.ai"
    submission_comment: str = "pass@1"
    judge_model: str = "claude-opus-4-6"

    def __post_init__(self) -> None:
        candidate_config_path = Path(self.agent_dir) / "config.toml"
        if candidate_config_path.exists():
            self.config_template_path = str(candidate_config_path.resolve())

        self._dataset_json_path = (
            Path(self.dataset_json).expanduser().resolve()
            if self.dataset_json
            else None
        )
        self._repository_dir_path = (
            Path(self.repository_dir).expanduser().resolve()
            if self.repository_dir
            else None
        )
        super().__post_init__()

    def _register_opensage_session(self, task: EvaluationTask):
        """Register a task session using a normalized per-run config copy.

        NYU benchmark runs often point at an external agent repo. That repo's
        config can contain Dockerfile paths that are only meaningful relative to
        the agent repo root, so we rewrite those paths in a temporary copy
        before handing the config to OpenSage.
        """
        config_template = Path(self.config_template_path).resolve()
        temp_dir = tempfile.mkdtemp(prefix=f"opensage_{task.session_id}_")
        temp_config_path = Path(temp_dir) / config_template.name
        shutil.copy(config_template, temp_config_path)

        template_variables = self._get_config_template_variables(task)
        self._replace_template_variables_in_config(temp_config_path, template_variables)
        self._normalize_nyuctf_config(temp_config_path, config_template)

        get_opensage_session(task.session_id, config_path=temp_config_path)
        shutil.rmtree(temp_dir, ignore_errors=True)

    def _normalize_nyuctf_config(
        self, temp_config_path: Path, source_config_path: Path
    ) -> None:
        """Rewrite agent config paths and networks for NYU benchmark runs."""
        content = temp_config_path.read_text()
        source_repo_root = source_config_path.parent.parent

        def replace_absolute_dockerfile(match: re.Match[str]) -> str:
            raw_value = match.group(1)
            dockerfile_path = Path(raw_value)
            if dockerfile_path.is_absolute():
                return match.group(0)
            resolved = (source_repo_root / dockerfile_path).resolve()
            return f'absolute_dockerfile_path = "{resolved}"'

        content = re.sub(
            r'^absolute_dockerfile_path = "([^"]+)"$',
            replace_absolute_dockerfile,
            content,
            flags=re.MULTILINE,
        )

        # Ensure the agent sandboxes can reach NYU challenge services on ctfnet.
        content = self._ensure_network_in_section(
            content, "sandbox.sandboxes.main", self.network_name
        )
        content = self._ensure_network_in_section(
            content, "sandbox.sandboxes.gdb_mcp", self.network_name
        )
        temp_config_path.write_text(content)

    def _ensure_network_in_section(
        self, content: str, section_name: str, network_name: str
    ) -> str:
        pattern = rf"(?ms)(^\[{re.escape(section_name)}\]\n)(.*?)(?=^\[|\Z)"
        match = re.search(pattern, content)
        if not match:
            return content

        header, body = match.group(1), match.group(2)
        if re.search(r'^network\s*=\s*".*"$', body, flags=re.MULTILINE):
            return content

        updated_section = f'{header}network = "{network_name}"\n{body}'
        return content[: match.start()] + updated_section + content[match.end() :]

    def _get_dataset(self) -> datasets.Dataset:
        _, challenges = load_dataset(
            split=self.split,
            challenge_name=self.challenge_name,
            dataset_json=self._dataset_json_path,
            repository_dir=self._repository_dir_path,
        )
        if self.max_challenges is not None:
            challenges = challenges[: self.max_challenges]

        samples = [challenge.to_sample() for challenge in challenges]
        samples = self._filter_pending_task(samples)

        if not samples:
            raise RuntimeError("No NYU CTF Bench challenges selected.")

        logger.warning("Loaded %d NYU CTF Bench challenges", len(samples))
        return datasets.Dataset.from_list(samples)

    def _filter_pending_task(
        self, samples: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Keep only challenges that do not already have an output entry.

        Reusing an existing `output_dir` acts like "continue from last run":
        any challenge with a corresponding directory entry is skipped.
        """
        output_root = Path(self.output_dir)
        if not output_root.exists():
            return samples

        existing = {
            path.name
            for path in output_root.iterdir()
            if path.is_dir() and path.name not in ("results", "__pycache__")
        }

        return [
            sample for sample in samples if sample["canonical_name"] not in existing
        ]

    def _create_task(self, sample: dict, model=None) -> EvaluationTask:
        challenge = LoadedChallenge.from_sample(sample)
        staged_dir = Path(
            tempfile.mkdtemp(prefix=f"opensage_nyuctf_{challenge.canonical_name}_")
        )
        challenge.stage_files(staged_dir)
        return EvaluationTask(
            id=challenge.canonical_name,
            sample=sample,
            first_user_message=build_challenge_prompt(challenge),
            output_dir=str(Path(self.output_dir) / challenge.canonical_name),
            initial_data_dir=str(staged_dir),
            export_dir_in_sandbox="/workspace",
            model=model,
        )

    def _log_task_banner(self, challenge: LoadedChallenge) -> None:
        """Emit a large log banner so the active NYU task is obvious in logs."""
        target = (
            f"{challenge.server_name}:{challenge.port}"
            if challenge.server_name and challenge.port
            else (challenge.server_name or "local-only")
        )
        lines = [
            "",
            "=" * 80,
            "RUNNING NYU CTF BENCH TASK",
            f"canonical_name : {challenge.canonical_name}",
            f"name           : {challenge.name}",
            f"category       : {challenge.category}",
            f"target         : {target}",
            "=" * 80,
        ]
        logger.warning("\n".join(lines))

    def _cleanup_failed_task_start(self, task: EvaluationTask) -> None:
        """Remove partial task output when challenge startup fails early."""
        output_path = Path(task.output_dir)
        if output_path.exists():
            shutil.rmtree(output_path, ignore_errors=True)

    def _cleanup_incomplete_task(self, task: EvaluationTask) -> None:
        """Remove task output directory if score.json was not generated.

        This ensures failed tasks are retried on the next run, since the
        resume mechanism skips tasks whose output directory already exists.
        """
        output_path = Path(task.output_dir)
        if output_path.exists() and not (output_path / "score.json").exists():
            logger.warning(
                "Task %s did not produce score.json — removing output directory for retry",
                task.id,
            )
            shutil.rmtree(output_path, ignore_errors=True)

    def _record_failed_task(
        self,
        task: EvaluationTask,
        challenge: LoadedChallenge,
        exc: BaseException,
    ) -> None:
        """Record an agent-side failure as a fully-scored (but unsolved) task.

        This keeps the resume mechanism honest: the task has a score.json,
        so the next run will not blindly re-attempt it. The transcript dump
        is best-effort — if the run died before a session trace was written,
        we still emit a transcript with an empty trajectory so aggregation
        code that depends on submission_trajectory/* doesn't miss the task.
        """
        output_path = Path(task.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        finished_at = datetime.datetime.now(datetime.UTC).isoformat()
        started_at = task.sample.get("_benchmark_started_at")
        duration_seconds: float | None = None
        if started_at:
            try:
                started_dt = datetime.datetime.fromisoformat(str(started_at))
                finished_dt = datetime.datetime.fromisoformat(finished_at)
                duration_seconds = (finished_dt - started_dt).total_seconds()
            except ValueError:
                duration_seconds = None

        score: dict[str, Any] = {
            "canonical_name": challenge.canonical_name,
            "split": challenge.split,
            "solved": False,
            "flag_correct": False,
            "matched_flag": None,
            "exit_reason": "task_error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "judge_pass": False,
            "judge_reason": "not_run",
            "judge_findings": [],
            "server_name": challenge.server_name,
            "port": challenge.port,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_seconds": duration_seconds,
        }
        (output_path / "score.json").write_text(json.dumps(score, indent=2) + "\n")

        session_trace = self._load_session_trace(output_path / "session_trace.json")
        transcript_payload = make_transcript_payload(
            challenge=challenge,
            score=score,
            session_trace=session_trace,
            prompt=build_challenge_prompt(challenge),
        )
        transcript_dir = output_path / "submission_trajectory"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        (transcript_dir / f"{challenge.canonical_name}.json").write_text(
            json.dumps(transcript_payload, indent=2) + "\n"
        )
        logger.warning(
            "Task %s failed with %s; recorded as task_error (score.json + transcript written)",
            task.id,
            type(exc).__name__,
        )

    async def _generate_one(self, task: EvaluationTask) -> dict:
        challenge = LoadedChallenge.from_sample(task.sample)
        self._log_task_banner(challenge)
        self._ensure_network_exists()
        task.sample["_benchmark_started_at"] = datetime.datetime.now(
            datetime.UTC
        ).isoformat()

        # Phase 1 — challenge environment startup. Failures here are usually
        # infrastructure-side (docker compose, network setup) and likely to
        # succeed on a future attempt, so we wipe any partial output and let
        # the resume mechanism retry the task on the next run.
        try:
            self._start_challenge(challenge)
        except Exception:
            if task.initial_data_dir:
                shutil.rmtree(task.initial_data_dir, ignore_errors=True)
            self._cleanup_failed_task_start(task)
            raise

        # Phase 2 — agent run + scoring. Failures here come from our side
        # (litellm, tool errors, sandbox crashes, etc.). We mark the task as
        # failed by writing score.json and a transcript so the resume
        # mechanism stops re-running it indefinitely, then re-raise so the
        # dispatcher still records the failure for this run.
        try:
            return await super()._generate_one(task)
        except Exception as exc:
            self._record_failed_task(task, challenge, exc)
            raise
        finally:
            # The challenge service lifecycle is outside OpenSage's sandbox manager,
            # so we must always tear it down even if the agent crashes mid-run.
            self._stop_challenge(challenge)
            if task.initial_data_dir:
                shutil.rmtree(task.initial_data_dir, ignore_errors=True)
            # Still useful for KeyboardInterrupt and other BaseException paths
            # that bypass _record_failed_task — those should remain retryable.
            self._cleanup_incomplete_task(task)

    def _get_task_id(self, sample: dict) -> str:
        return str(sample["canonical_name"])

    def _get_first_user_message(self, sample: dict) -> str:
        return build_challenge_prompt(LoadedChallenge.from_sample(sample))

    def _get_export_dir_in_sandbox(self, sample: dict) -> str | None:
        return "/workspace"

    def _get_config_template_variables(self, task: EvaluationTask) -> dict:
        # Respect the selected agent config's own TASK_NAME. We only inject the
        # shared challenge data mount for the benchmark workspace.
        template = {}
        if task.initial_data_dir:
            template["ABSOLUTE_SHARED_DATA_PATH"] = str(
                Path(task.initial_data_dir).resolve()
            )
        return template

    async def _collect_outputs(self, task: EvaluationTask, session: Session) -> dict:
        info = await super()._collect_outputs(task, session)
        challenge = LoadedChallenge.from_sample(task.sample)
        prompt = build_challenge_prompt(challenge)
        score = self._score_task(task, challenge, prompt)
        info["score"] = score

        output_path = Path(task.output_dir)
        (output_path / "score.json").write_text(json.dumps(score, indent=2) + "\n")

        session_trace = self._load_session_trace(output_path / "session_trace.json")
        transcript_payload = make_transcript_payload(
            challenge=challenge,
            score=score,
            session_trace=session_trace,
            prompt=prompt,
        )
        transcript_dir = output_path / "submission_trajectory"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        (transcript_dir / f"{challenge.canonical_name}.json").write_text(
            json.dumps(transcript_payload, indent=2) + "\n"
        )
        return info

    def _ensure_network_exists(self) -> None:
        inspect = run_command(
            ["docker", "network", "inspect", self.network_name],
            check=False,
        )
        if inspect.returncode != 0:
            run_command(["docker", "network", "create", self.network_name])

    def _start_challenge(self, challenge: LoadedChallenge) -> None:
        if not challenge.compose:
            return
        compose_file = challenge.challenge_dir / "docker-compose.yml"
        run_command(
            [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "up",
                "-d",
                "--force-recreate",
            ],
            cwd=challenge.challenge_dir,
        )
        logger.info(
            "Starting challenge %s with compose: %s",
            challenge.canonical_name,
            compose_file,
        )
        self._connect_challenge_containers_to_network(challenge)

    def _stop_challenge(self, challenge: LoadedChallenge) -> None:
        if not challenge.compose:
            return
        compose_file = challenge.challenge_dir / "docker-compose.yml"
        run_command(
            ["docker", "compose", "-f", str(compose_file), "down", "--volumes"],
            cwd=challenge.challenge_dir,
            check=False,
        )

    def _connect_challenge_containers_to_network(
        self, challenge: LoadedChallenge
    ) -> None:
        """Attach compose containers to the shared benchmark network.

        OpenSage's CTF sandbox runs on `ctfnet`. NYU_CTF_Bench challenge services
        are started with plain `docker compose`, so we explicitly bridge them onto
        the same network after startup and attach the challenge hostname as an alias.
        """
        compose_file = challenge.challenge_dir / "docker-compose.yml"
        container_ids = self._compose_container_ids(
            compose_file, challenge.challenge_dir
        )
        if not container_ids:
            return

        aliased = False
        if challenge.server_name:
            preferred = run_command(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(compose_file),
                    "ps",
                    "-q",
                    challenge.server_name,
                ],
                cwd=challenge.challenge_dir,
                check=False,
            )
            for container_id in preferred.stdout.split():
                self._connect_container(container_id, alias=challenge.server_name)
                aliased = True

        for container_id in container_ids:
            self._connect_container(
                container_id,
                alias=challenge.server_name
                if challenge.server_name and not aliased
                else None,
            )
            aliased = aliased or bool(challenge.server_name)

    def _compose_container_ids(self, compose_file: Path, cwd: Path) -> list[str]:
        result = run_command(
            ["docker", "compose", "-f", str(compose_file), "ps", "-q"],
            cwd=cwd,
            check=False,
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def _connect_container(self, container_id: str, alias: str | None = None) -> None:
        cmd = ["docker", "network", "connect"]
        if alias:
            cmd.extend(["--alias", alias])
        cmd.extend([self.network_name, container_id])
        run_command(cmd, check=False)

    def _score_task(
        self, task: EvaluationTask, challenge: LoadedChallenge, prompt: str
    ) -> dict[str, Any]:
        output_path = Path(task.output_dir)
        final_flag_candidates = [
            output_path / "sandbox_output" / "final_flag.txt",
            output_path / "sandbox_output" / "workspace" / "final_flag.txt",
        ]
        captured_texts: list[str] = []
        for candidate in final_flag_candidates:
            if candidate.exists():
                captured_texts.append(candidate.read_text(errors="replace"))

        session_trace = None
        session_trace_path = output_path / "session_trace.json"
        if session_trace_path.exists():
            captured_texts.append(session_trace_path.read_text(errors="replace"))
            session_trace = self._load_session_trace(session_trace_path)

        flag_correct, matched_flag = find_exact_flag(challenge.flag, *captured_texts)
        judge = {
            "pass": False,
            "reason": "not_run",
            "findings": [],
        }
        if flag_correct and matched_flag:
            judge = judge_trajectory_sync(
                challenge=challenge,
                prompt=prompt,
                matched_flag=matched_flag,
                session_trace=session_trace,
                model_name=self.judge_model,
            )
        # Only a successful negative judgment should override an exact flag match.
        # Judge infrastructure failures are tracked in metadata but do not
        # invalidate an otherwise correct solve.
        judge_pass = bool(judge["pass"]) or judge["reason"] == "judge_error"
        solved = flag_correct and judge_pass

        metadata = self._load_json_file(output_path / "metadata.json")
        session_data = metadata.get("session", {}) if isinstance(metadata, dict) else {}
        session_state = (
            session_data.get("state", {}) if isinstance(session_data, dict) else {}
        )
        adk_state = (
            session_state.get("_adk", {}) if isinstance(session_state, dict) else {}
        )
        llm_calls_used = int(adk_state.get("llm_calls_used", 0) or 0)
        finished_at = datetime.datetime.now(datetime.UTC).isoformat()
        started_at = task.sample.get("_benchmark_started_at") or session_data.get(
            "last_update_time"
        )
        duration_seconds = None
        if started_at:
            try:
                started_dt = datetime.datetime.fromisoformat(str(started_at))
                finished_dt = datetime.datetime.fromisoformat(finished_at)
                duration_seconds = (finished_dt - started_dt).total_seconds()
            except ValueError:
                duration_seconds = None
        exit_reason = "unknown"
        if solved:
            exit_reason = "solved"
        elif flag_correct and not judge_pass:
            exit_reason = "judge_rejected"
        elif self.max_llm_calls > 0 and llm_calls_used >= self.max_llm_calls:
            exit_reason = "cost"
        elif session_state.get("task_finished", False):
            exit_reason = "finished"
        return {
            "canonical_name": challenge.canonical_name,
            "split": challenge.split,
            "solved": solved,
            "flag_correct": flag_correct,
            "matched_flag": matched_flag,
            "exit_reason": exit_reason,
            "judge_pass": judge_pass,
            "judge_reason": judge["reason"],
            "judge_findings": judge["findings"],
            "server_name": challenge.server_name,
            "port": challenge.port,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_seconds": duration_seconds,
        }

    def evaluate(self) -> dict[str, Any]:
        _, selected_challenges = load_dataset(
            split=self.split,
            challenge_name=self.challenge_name,
            dataset_json=self._dataset_json_path,
            repository_dir=self._repository_dir_path,
        )
        if self.max_challenges is not None:
            selected_challenges = selected_challenges[: self.max_challenges]

        output_root = Path(self.output_dir)
        results_map: dict[str, bool] = {}
        transcript_root = output_root / "results" / "leaderboard_submission"
        transcript_root.mkdir(parents=True, exist_ok=True)

        solved_count = 0
        included_count = 0
        for challenge in selected_challenges:
            task_dir = output_root / challenge.canonical_name
            score = self._load_json_file(task_dir / "score.json")
            source_json = (
                task_dir / "submission_trajectory" / f"{challenge.canonical_name}.json"
            )
            if not score or not source_json.exists():
                continue

            included_count += 1
            solved = bool(score.get("solved"))
            solved_count += int(solved)
            results_map[challenge.canonical_name] = solved

            shutil.copy2(source_json, transcript_root / source_json.name)

        summary = {
            "metadata": {
                "agent": self.submission_agent,
                "comment": self.submission_comment,
                "model": self.submission_model,
                "link": self.submission_link,
                "date": datetime.datetime.now(datetime.UTC).strftime("%Y/%m/%d"),
            },
            "results": results_map,
        }

        submission_root = output_root / "results" / "leaderboard_submission"
        submission_root.mkdir(parents=True, exist_ok=True)
        (submission_root / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n"
        )

        evaluation_results = {
            "total": included_count,
            "solved": solved_count,
            "success_rate": solved_count / included_count if included_count else 0.0,
            "submission_dir": str(submission_root),
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        (output_root / "results" / "evaluation_results.json").write_text(
            json.dumps(evaluation_results, indent=2) + "\n"
        )
        return evaluation_results

    def _load_json_file(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text())

    def _load_session_trace(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        return json.loads(path.read_text())


if __name__ == "__main__":
    fire.Fire(NYU_CTF_Bench)
