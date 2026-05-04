"""Claude Code baseline driver for the SAGE-CCB benchmark.

This runner starts every selected SAGE-CCB challenge at the same time on
ctfnet, runs a single Claude Code session with the full challenge list, and
scores the shared /workspace outputs.

Usage examples:

    python benchmarks/sage-ccb/cc-baseline.py build
    python benchmarks/sage-ccb/cc-baseline.py run --output_dir evals/cc/<id>
    python benchmarks/sage-ccb/cc-baseline.py run_debug --challenge_name 2026-rev-sun_temple
    python benchmarks/sage-ccb/cc-baseline.py evaluate --output_dir evals/cc/<id>
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
THIS_DIR = Path(__file__).resolve().parent
for candidate in (str(PROJECT_ROOT), str(THIS_DIR)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import fire  # noqa: E402
from helpers import (  # noqa: E402
    DEFAULT_NETWORK,
    DEFAULT_REPOSITORY_DIR,
    DEFAULT_TIMEOUT,
    LoadedChallenge,
    build_suite_prompt,
    load_sage_ccb_challenges,
    parse_timeout,
    run_command,
    score_and_write_results,
    stage_challenge_suite,
    start_challenge_services,
    stop_challenge_services,
    stream_json_to_session_trace,
    write_raw_run_artifacts,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sage-ccb-cc-baseline")

BASELINE_IMAGE_DIR = THIS_DIR / "baseline-image"
ENV_FILE = BASELINE_IMAGE_DIR / ".env"
DEFAULT_IMAGE_TAG = "ghcr.io/opensage-agent/cc-sage-ccb:latest"
DEFAULT_CLAUDE_CODE_MODEL = "claude-opus-4-6"
DEFAULT_MAX_TURNS = 600


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


@dataclass(kw_only=True)
class ClaudeCodeSAGECCB:
    """Claude Code baseline runner for the multi-challenge SAGE-CCB suite."""

    output_dir: str = ""
    challenge_name: str | None = None
    dataset_json: str | None = None
    repository_dir: str | None = str(DEFAULT_REPOSITORY_DIR)
    max_challenges: int | None = None

    image_tag: str = DEFAULT_IMAGE_TAG
    network_name: str = DEFAULT_NETWORK
    remove_host_ports: bool = True
    skip_build: bool = False

    max_turns: int = DEFAULT_MAX_TURNS
    timeout: str = DEFAULT_TIMEOUT
    permission_mode: str = "bypassPermissions"

    judge_model: str = "claude-opus-4-6"
    judge_error_is_pass: bool = False

    def __post_init__(self) -> None:
        if not self.output_dir:
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            self.output_dir = str(
                PROJECT_ROOT / "evals" / "sage-ccb-cc-baseline" / stamp
            )
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        self._dataset_json = (
            Path(self.dataset_json).expanduser().resolve()
            if self.dataset_json
            else None
        )
        self._repository_dir = (
            Path(self.repository_dir).expanduser().resolve()
            if self.repository_dir
            else None
        )

    def build(self) -> str:
        """Build the cc-baseline Docker image."""
        logger.info("Building image %s from %s", self.image_tag, BASELINE_IMAGE_DIR)
        subprocess.run(
            ["docker", "build", "-t", self.image_tag, str(BASELINE_IMAGE_DIR)],
            check=True,
        )
        return self.image_tag

    def run_debug(self, challenge_name: str) -> dict[str, Any]:
        """Run a selected challenge or comma-separated challenge list."""
        self.challenge_name = challenge_name
        return self.run()

    def run(self) -> dict[str, Any]:
        """Run a single Claude Code session against all selected challenges.

        This method is concerned only with producing raw artifacts under
        `<output_dir>/raw/`. Flag matching and the LLM judge are delegated to
        `evaluate()`, which is invoked at the end so the default UX is
        unchanged but `evaluate` can be re-run independently to refresh
        scoring without re-running Claude Code.
        """
        if not self.skip_build:
            self.build()

        challenges = self._load_challenges()
        logger.info("Running SAGE-CCB suite with %d challenge(s)", len(challenges))

        suite_dir = Path(self.output_dir)
        sandbox_dir = suite_dir / "sandbox_output"
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        suite_prompt = build_suite_prompt(challenges)
        (sandbox_dir / "prompt.txt").write_text(suite_prompt)

        staged_dir = Path(tempfile.mkdtemp(prefix="sage_ccb_cc_"))
        stage_challenge_suite(challenges, staged_dir)

        started_at = _utcnow_iso()
        run_started = time.time()
        timeout_limit = parse_timeout(self.timeout)
        run_deadline = run_started + timeout_limit
        started_services = []
        suite_exit_reason = "finished"
        exit_code = 0
        error: dict[str, Any] | None = None

        try:
            started_services = start_challenge_services(
                challenges,
                network_name=self.network_name,
                compose_output_dir=suite_dir / "compose",
                remove_host_ports=self.remove_host_ports,
                startup_deadline=run_deadline,
            )
            exit_code = self._invoke_claude_code(
                prompt_path=sandbox_dir / "prompt.txt",
                staged_dir=staged_dir,
                sandbox_dir=sandbox_dir,
                timeout=max(1, int(run_deadline - time.time())),
            )
            if exit_code == 124:
                suite_exit_reason = "timeout"
            elif exit_code != 0:
                suite_exit_reason = "claude_error"
        except TimeoutError as exc:
            logger.exception("SAGE-CCB Claude Code run timed out: %s", exc)
            suite_exit_reason = "timeout"
            error = {"type": type(exc).__name__, "message": str(exc)}
        except Exception as exc:
            logger.exception("SAGE-CCB Claude Code run failed: %s", exc)
            suite_exit_reason = "task_error"
            error = {"type": type(exc).__name__, "message": str(exc)}
        finally:
            stop_challenge_services(started_services)
            shutil.rmtree(staged_dir, ignore_errors=True)

        finished_at = _utcnow_iso()
        session_trace = stream_json_to_session_trace(
            sandbox_dir / "claude_stream.jsonl"
        )
        (suite_dir / "session_trace.json").write_text(
            json.dumps(session_trace, indent=2) + "\n"
        )
        (suite_dir / "claude_exit_code.json").write_text(
            json.dumps({"exit_code": exit_code}, indent=2) + "\n"
        )

        write_raw_run_artifacts(
            output_dir=suite_dir,
            sandbox_dir=sandbox_dir,
            suite_prompt=suite_prompt,
            session_trace=session_trace,
            suite_exit_reason=suite_exit_reason,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=time.time() - run_started,
            driver="claude-code",
            error=error,
        )
        return self.evaluate()

    def evaluate(self) -> dict[str, Any]:
        """Score raw artifacts and run the LLM judge.

        Reads the workspace and session trace written by `run()` under
        `<output_dir>/raw/`, runs flag matching and the reward-hacking judge,
        and writes `<output_dir>/results/`. Safe to re-run after fixing judge
        code — it only rewrites results/ and raw/judge/.
        """
        suite_dir = Path(self.output_dir).resolve()
        return score_and_write_results(
            output_dir=suite_dir,
            challenges=self._load_challenges(),
            judge_model=self.judge_model,
            judge_error_is_pass=self.judge_error_is_pass,
        )

    def _load_challenges(self) -> list[LoadedChallenge]:
        _, challenges = load_sage_ccb_challenges(
            challenge_name=self.challenge_name,
            dataset_json=self._dataset_json,
            repository_dir=self._repository_dir,
            max_challenges=self.max_challenges,
        )
        if not challenges:
            raise RuntimeError("No SAGE-CCB challenges selected.")
        return challenges

    def _invoke_claude_code(
        self,
        *,
        prompt_path: Path,
        staged_dir: Path,
        sandbox_dir: Path,
        timeout: int | None = None,
    ) -> int:
        """Run Claude Code once against the full benchmark prompt."""
        container_name = f"sage-ccb-cc-{uuid.uuid4().hex[:8]}"
        inner_cmd = (
            "set -o pipefail; "
            f"cat {prompt_path.name} | claude --print "
            f"--model {DEFAULT_CLAUDE_CODE_MODEL} "
            f"--max-turns {self.max_turns} "
            "--dangerously-skip-permissions "
            f"--permission-mode {self.permission_mode} "
            "--output-format stream-json --verbose "
            "> /workspace/claude_stream.jsonl 2> /workspace/claude_stderr.log"
        )

        cmd = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            self.network_name,
            "-v",
            f"{staged_dir.resolve()}:/shared:ro",
            "-v",
            f"{sandbox_dir.resolve()}:/workspace",
        ]
        cmd.extend(self._env_file_args())
        cmd.extend([self.image_tag, "bash", "-lc", inner_cmd])

        logger.info(
            "Invoking Claude Code for SAGE-CCB suite (timeout=%s)",
            self.timeout,
        )
        try:
            completed = subprocess.run(
                cmd,
                timeout=timeout or parse_timeout(self.timeout),
                check=False,
            )
            return completed.returncode
        except subprocess.TimeoutExpired:
            logger.warning("Claude Code suite timed out; killing %s", container_name)
            run_command(["docker", "kill", container_name], check=False)
            return 124
        finally:
            self._chown_workspace_to_host(sandbox_dir)

    def _chown_workspace_to_host(self, sandbox_dir: Path) -> None:
        if not hasattr(os, "getuid"):
            return
        uid, gid = os.getuid(), os.getgid()
        if uid == 0:
            return
        run_command(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{sandbox_dir.resolve()}:/workspace",
                "--entrypoint",
                "chown",
                self.image_tag,
                "-R",
                f"{uid}:{gid}",
                "/workspace",
            ],
            check=False,
        )

    def _env_file_args(self) -> list[str]:
        if not ENV_FILE.exists():
            raise FileNotFoundError(
                f"Expected {ENV_FILE} to exist. Copy "
                f"{BASELINE_IMAGE_DIR / '.env.template'} to .env and fill in "
                "Claude Code credentials."
            )
        return ["--env-file", str(ENV_FILE)]


if __name__ == "__main__":
    fire.Fire(ClaudeCodeSAGECCB)
