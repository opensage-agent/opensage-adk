from __future__ import annotations

import datetime
import json
import logging
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from opensage.utils.project_info import PROJECT_PATH

from . import helpers

logger = logging.getLogger(__name__)

Provider = Literal["claude", "codex"]
ProviderAlias = Literal["claude", "claude-code", "codex"]
ExecutionMode = Literal["docker", "local"]

CLAUDE_IMAGE_DIR = Path(__file__).resolve().parent / "claude-image"
CODEX_IMAGE_DIR = Path(__file__).resolve().parent / "codex-image"
DEFAULT_CLAUDE_CODE_MODEL = "claude-opus-4-8"
DEFAULT_CODEX_MODEL = "gpt-5.5"
DEFAULT_REASONING_EFFORT = "high"
DEFAULT_IMAGE_TAGS: dict[Provider, str] = {
    "claude": "opensage-cybench-claude:latest",
    "codex": "opensage-cybench-codex:latest",
}


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(errors="replace"))
    except Exception:
        return None


def _reset_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _copy_path(src: Path, dst: Path) -> None:
    _reset_path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def _quote(value: str | Path) -> str:
    return shlex.quote(str(value))


def _default_model(provider: Provider) -> str:
    return DEFAULT_CLAUDE_CODE_MODEL if provider == "claude" else DEFAULT_CODEX_MODEL


def _normalize_provider(provider: ProviderAlias | str) -> Provider:
    normalized = str(provider).strip().lower().replace("_", "-")
    if normalized in {"claude", "claude-code"}:
        return "claude"
    if normalized == "codex":
        return "codex"
    raise ValueError("provider must be one of: claude-code, claude, codex")


def _image_dir(provider: Provider) -> Path:
    return CLAUDE_IMAGE_DIR if provider == "claude" else CODEX_IMAGE_DIR


def _default_image_tag(provider: Provider) -> str:
    return DEFAULT_IMAGE_TAGS[provider]


def _default_env_file(provider: Provider) -> str:
    return str(CLAUDE_IMAGE_DIR / ".env") if provider == "claude" else ""


@dataclass
class BudgetSnapshot:
    configured_budget_usd: float
    spent_usd: float = 0.0
    source: str = "unavailable"
    is_estimate: bool = False
    estimate_method: str | None = None
    budget_exhausted: bool = False
    exhausted_reason: str | None = None
    tokens: dict[str, int] = field(default_factory=dict)
    pricing: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["remaining_budget_usd"] = max(
            0.0, self.configured_budget_usd - self.spent_usd
        )
        return payload


class BudgetTracker:
    def __init__(
        self,
        *,
        budget_usd: float,
        input_cost_per_million: float = 1.25,
        cached_input_cost_per_million: float = 0.125,
        output_cost_per_million: float = 10.0,
        reasoning_output_cost_per_million: float | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._budget_usd = float(budget_usd)
        self._spent_usd = 0.0
        self._source = "unavailable"
        self._is_estimate = False
        self._estimate_method: str | None = None
        self._tokens: dict[str, int] = {}
        self._pricing = {
            "input_cost_per_million": float(input_cost_per_million),
            "cached_input_cost_per_million": float(cached_input_cost_per_million),
            "output_cost_per_million": float(output_cost_per_million),
            "reasoning_output_cost_per_million": float(
                output_cost_per_million
                if reasoning_output_cost_per_million is None
                else reasoning_output_cost_per_million
            ),
        }

    @property
    def budget_exhausted(self) -> bool:
        with self._lock:
            return self._spent_usd >= self._budget_usd

    def observe_line(self, raw_line: str) -> None:
        line = raw_line.strip()
        if not line.startswith("{"):
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return
        self.observe_payload(payload)

    def observe_payload(self, payload: Any) -> None:
        cost = _find_largest_number(
            payload,
            {"total_cost_usd", "cost_usd", "spent_usd", "total_spent_usd"},
        )
        tokens = _extract_token_usage(payload)
        with self._lock:
            for key, value in tokens.items():
                self._tokens[key] = max(int(value), self._tokens.get(key, 0))
            if cost is not None:
                self._spent_usd = max(self._spent_usd, float(cost))
                self._source = "cli_cost"
                self._is_estimate = False
                self._estimate_method = None
                return
            estimated = self._estimate_from_tokens_locked()
            if estimated is not None:
                self._spent_usd = max(self._spent_usd, estimated)
                if self._source != "cli_cost":
                    self._source = "token_usage"
                    self._is_estimate = True
                    self._estimate_method = "configured_per_million_token_rates"

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            exhausted = self._spent_usd >= self._budget_usd
            return BudgetSnapshot(
                configured_budget_usd=self._budget_usd,
                spent_usd=self._spent_usd,
                source=self._source,
                is_estimate=self._is_estimate,
                estimate_method=self._estimate_method,
                budget_exhausted=exhausted,
                exhausted_reason="budget_exhausted" if exhausted else None,
                tokens=dict(self._tokens),
                pricing=dict(self._pricing),
            )

    def _estimate_from_tokens_locked(self) -> float | None:
        if not self._tokens:
            return None
        cached = self._tokens.get("cached_input_tokens", 0)
        input_tokens = max(0, self._tokens.get("input_tokens", 0) - cached)
        output_tokens = self._tokens.get("output_tokens", 0)
        reasoning_tokens = self._tokens.get("reasoning_output_tokens", 0)
        return (
            input_tokens * self._pricing["input_cost_per_million"]
            + cached * self._pricing["cached_input_cost_per_million"]
            + output_tokens * self._pricing["output_cost_per_million"]
            + reasoning_tokens * self._pricing["reasoning_output_cost_per_million"]
        ) / 1_000_000.0


def _find_largest_number(payload: Any, names: set[str]) -> float | None:
    found: list[float] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in names and isinstance(item, (int, float)):
                    found.append(float(item))
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return max(found) if found else None


def _extract_token_usage(payload: Any) -> dict[str, int]:
    aliases = {
        "input_tokens": "input_tokens",
        "prompt_tokens": "input_tokens",
        "total_input_tokens": "input_tokens",
        "cached_input_tokens": "cached_input_tokens",
        "cached_prompt_tokens": "cached_input_tokens",
        "output_tokens": "output_tokens",
        "completion_tokens": "output_tokens",
        "total_output_tokens": "output_tokens",
        "reasoning_output_tokens": "reasoning_output_tokens",
        "reasoning_tokens": "reasoning_output_tokens",
        "total_tokens": "total_tokens",
    }
    result: dict[str, int] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = aliases.get(key)
                if normalized and isinstance(item, (int, float)):
                    result[normalized] = max(int(item), result.get(normalized, 0))
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return result


@dataclass
class ProcessResult:
    exit_code: int
    exit_reason: str
    timed_out: bool
    budget_exhausted: bool
    budget: BudgetSnapshot
    started_at: str
    finished_at: str
    duration_seconds: float


def _run_process(
    cmd: list[str],
    *,
    timeout_seconds: int,
    budget_tracker: BudgetTracker,
    stdout_path: Path,
    stderr_path: Path,
    cwd: Path | None = None,
    kill_callback: Callable[[], None] | None = None,
) -> ProcessResult:
    started_at = _utcnow_iso()
    started = time.time()
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
    )

    def stream(pipe, target: Path, on_line: Callable[[str], None] | None) -> None:
        with target.open("w") as handle:
            if pipe is None:
                return
            for line in pipe:
                handle.write(line)
                handle.flush()
                if on_line is not None:
                    on_line(line)

    stdout_thread = threading.Thread(
        target=stream, args=(proc.stdout, stdout_path, budget_tracker.observe_line)
    )
    stderr_thread = threading.Thread(
        target=stream, args=(proc.stderr, stderr_path, None)
    )
    stdout_thread.start()
    stderr_thread.start()

    exit_reason = "finished"
    timed_out = False
    budget_exhausted = False
    deadline = started + timeout_seconds
    while proc.poll() is None:
        if time.time() >= deadline:
            timed_out = True
            exit_reason = "timeout"
            _terminate_process(proc, kill_callback=kill_callback)
            break
        if budget_tracker.budget_exhausted:
            budget_exhausted = True
            exit_reason = "budget_exhausted"
            _terminate_process(proc, kill_callback=kill_callback)
            break
        time.sleep(0.25)

    try:
        exit_code = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _kill_process(proc, kill_callback=kill_callback)
        exit_code = proc.poll()
        if exit_code is None:
            exit_code = -signal.SIGKILL
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)

    if exit_code != 0 and exit_reason == "finished":
        exit_reason = "agent_error"
    finished_at = _utcnow_iso()
    budget = budget_tracker.snapshot()
    if budget.budget_exhausted and exit_reason == "finished":
        exit_reason = "budget_exhausted"
        budget_exhausted = True
    return ProcessResult(
        exit_code=int(exit_code),
        exit_reason=exit_reason,
        timed_out=timed_out,
        budget_exhausted=budget_exhausted or budget.budget_exhausted,
        budget=budget,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=time.time() - started,
    )


def _terminate_process(
    proc: subprocess.Popen[str], *, kill_callback: Callable[[], None] | None
) -> None:
    if kill_callback is not None:
        kill_callback()
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        proc.terminate()


def _kill_process(
    proc: subprocess.Popen[str], *, kill_callback: Callable[[], None] | None
) -> None:
    if kill_callback is not None:
        kill_callback()
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        proc.kill()


def jsonl_to_session_trace(path: Path, *, provider: Provider) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return {"events": events}
    for raw_line in path.read_text(errors="replace").splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if provider == "claude":
            _append_claude_event(events, payload)
        else:
            _append_codex_event(events, payload)
    return {"events": events}


def _timestamp(payload: dict[str, Any]) -> float:
    ts = payload.get("timestamp") or payload.get("started_at_ms")
    if isinstance(ts, (int, float)):
        return float(ts / 1000.0 if ts > 10_000_000_000 else ts)
    return time.time()


def _append_claude_event(events: list[dict[str, Any]], payload: dict[str, Any]) -> None:
    kind = payload.get("type")
    ts = _timestamp(payload)
    if kind == "user":
        message = payload.get("message", {}) or {}
        parts = _claude_parts(message.get("content"))
        author = "user"
    elif kind == "assistant":
        message = payload.get("message", {}) or {}
        parts = _claude_parts(message.get("content"))
        author = "assistant"
    elif kind == "result" and isinstance(payload.get("result"), str):
        parts = [{"text": payload["result"]}]
        author = "assistant"
    else:
        return
    if parts:
        events.append(
            {
                "author": author,
                "timestamp": ts,
                "content": {"role": author, "parts": parts},
            }
        )


def _claude_parts(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str) and content:
        return [{"text": content}]
    if not isinstance(content, list):
        return []
    parts: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "text" and item.get("text"):
            parts.append({"text": item.get("text")})
        elif item_type == "tool_use":
            parts.append(
                {
                    "function_call": {
                        "name": item.get("name", ""),
                        "args": item.get("input", {}) or {},
                    }
                }
            )
        elif item_type == "tool_result":
            response = item.get("content")
            if isinstance(response, list):
                response = "\n".join(
                    block.get("text", "")
                    for block in response
                    if isinstance(block, dict)
                )
            parts.append(
                {
                    "function_response": {
                        "name": item.get("tool_use_id") or "tool_result",
                        "response": response,
                    }
                }
            )
    return parts


def _append_codex_event(events: list[dict[str, Any]], payload: dict[str, Any]) -> None:
    text = _first_string(payload, {"text", "message", "content", "formatted_output"})
    event_type = str(payload.get("type") or payload.get("event") or "")
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"text": text})
    if "tool_call" in event_type or "exec_command_begin" in event_type:
        parts.append(
            {
                "function_call": {
                    "name": _first_string(payload, {"name", "tool_name", "namespace"})
                    or event_type
                    or "tool_call",
                    "args": _first_dict(payload, {"args", "arguments", "input"}) or {},
                }
            }
        )
    if "tool_call_output" in event_type or "exec_command_end" in event_type:
        parts.append(
            {
                "function_response": {
                    "name": _first_string(payload, {"name", "tool_name", "namespace"})
                    or event_type
                    or "tool_response",
                    "response": _first_value(
                        payload, {"output", "stdout", "stderr", "response"}
                    ),
                }
            }
        )
    if parts:
        events.append(
            {
                "author": "assistant",
                "timestamp": _timestamp(payload),
                "content": {"role": "assistant", "parts": parts},
            }
        )


def _first_value(payload: Any, keys: set[str]) -> Any:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and value not in (None, ""):
                return value
        for value in payload.values():
            found = _first_value(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _first_value(item, keys)
            if found not in (None, ""):
                return found
    return None


def _first_string(payload: Any, keys: set[str]) -> str | None:
    value = _first_value(payload, keys)
    if isinstance(value, str):
        return value
    return None


def _first_dict(payload: Any, keys: set[str]) -> dict[str, Any] | None:
    value = _first_value(payload, keys)
    return value if isinstance(value, dict) else None


@dataclass(kw_only=True)
class CyBenchBaseline:
    agent: ProviderAlias = "codex"
    provider: Provider = field(init=False)
    output_dir: str = ""
    model: str = field(init=False)
    reasoning_effort: str = field(init=False)
    time_limit: str = helpers.DEFAULT_TIME_LIMIT
    budget: float = 100.0
    image_tag: str = ""
    execution_mode: ExecutionMode = "docker"
    env_file: str | None = None
    skip_build: bool = False
    permission_mode: str = "bypassPermissions"

    bench_dir: str = ""
    task_list: str = helpers.DEFAULT_TASK_LIST
    challenge_name: str | None = None
    max_challenges: int | None = None
    easy_prompt: bool = False
    network_name: str = "shared_net"
    remove_host_ports: bool = True
    skip_services: bool = False
    rebuild_service_images: bool = False
    stage_script_timeout: int = 180
    force_rerun: bool = False
    max_workers: int = 1

    input_cost_per_million: float = 5
    cached_input_cost_per_million: float = 0.5
    output_cost_per_million: float = 30
    reasoning_output_cost_per_million: float | None = None

    def __post_init__(self) -> None:
        self.provider = _normalize_provider(self.agent)
        self.model = _default_model(self.provider)
        self.reasoning_effort = DEFAULT_REASONING_EFFORT
        self.image_tag = self.image_tag or _default_image_tag(self.provider)
        if self.env_file is None:
            self.env_file = _default_env_file(self.provider)
        self.budget = float(self.budget)
        if self.budget <= 0:
            raise ValueError("budget must be positive")
        self.max_workers = int(self.max_workers)
        if self.max_workers <= 0:
            raise ValueError("max_workers must be positive")
        self._time_limit_seconds = helpers.parse_time_limit(self.time_limit)
        self._cybench_dir = Path(self.bench_dir).expanduser().resolve()
        self._task_list_path: Path | None = None
        self._selected_tasks_cache: list[helpers.LoadedCybenchTask] | None = None
        if not self.output_dir:
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            self.output_dir = str(
                PROJECT_PATH / "evals" / f"cybench-{self.provider}-baseline" / stamp
            )
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)

    def build(self) -> str:
        image_dir = _image_dir(self.provider)
        logger.info("Building %s from %s", self.image_tag, image_dir)
        subprocess.run(
            ["docker", "build", "-t", self.image_tag, str(image_dir)],
            check=True,
        )
        return self.image_tag

    def run_debug(self, challenge_name: str | None = None) -> dict[str, Any]:
        if challenge_name is not None:
            self.challenge_name = challenge_name
        if not self.challenge_name:
            raise ValueError("run_debug requires challenge_name")
        return self.run()

    def run(self) -> dict[str, Any]:
        if self.execution_mode == "docker" and not self.skip_build:
            self.build()
        started_at = _utcnow_iso()
        tasks = self._load_tasks()
        scores_by_index: dict[int, dict[str, Any]] = {}
        pending: list[tuple[int, helpers.LoadedCybenchTask, Path]] = []
        for index, task in enumerate(tasks):
            output_dir = Path(self.output_dir) / helpers.task_output_dir_name(task)
            if self.force_rerun and output_dir.exists():
                helpers.reset_output_path_for_rerun(output_dir)
            elif output_dir.exists():
                continue
            pending.append((index, task, output_dir))
        if pending:
            self._prepare_parallel_run()
            if self.max_workers == 1 or len(pending) == 1:
                for index, task, output_dir in pending:
                    scores_by_index[index] = self._run_one(task, output_dir)
            else:
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    future_to_index = {
                        executor.submit(self._run_one, task, output_dir): index
                        for index, task, output_dir in pending
                    }
                    for future in as_completed(future_to_index):
                        scores_by_index[future_to_index[future]] = future.result()
        scores = [
            scores_by_index[index]
            for index in range(len(tasks))
            if index in scores_by_index
        ]
        finished_at = _utcnow_iso()
        summary = self._write_report(
            output_dir=Path(self.output_dir),
            tasks=tasks,
            scores=scores,
            agent_dir=Path(__file__).resolve().parent,
            cybench_dir=self._cybench_dir,
            task_list_path=self._task_list_path or Path(self.task_list),
            time_limit_seconds=self._time_limit_seconds,
            budget_usd=self.budget,
            started_at=started_at,
            finished_at=finished_at,
            challenge_name=self.challenge_name,
            max_challenges=self.max_challenges,
        )
        summary["max_workers"] = self.max_workers
        self._update_report_parallelism(Path(self.output_dir), self.max_workers)
        return summary

    def _prepare_parallel_run(self) -> None:
        if self.execution_mode == "docker":
            helpers.ensure_docker_network(self.network_name)

    def evaluate(self) -> dict[str, Any]:
        tasks = self._load_tasks()
        scores = []
        for task in tasks:
            output_dir = Path(self.output_dir) / helpers.task_output_dir_name(task)
            if not (output_dir / "score.json").exists():
                continue
            score = helpers.score_task(
                output_dir=output_dir,
                task=task,
            )
            scores.append(score)
        now = _utcnow_iso()
        summary = self._write_report(
            output_dir=Path(self.output_dir),
            tasks=tasks,
            scores=scores,
            agent_dir=Path(__file__).resolve().parent,
            cybench_dir=self._cybench_dir,
            task_list_path=self._task_list_path or Path(self.task_list),
            time_limit_seconds=self._time_limit_seconds,
            budget_usd=self.budget,
            started_at=now,
            finished_at=now,
            challenge_name=self.challenge_name,
            max_challenges=self.max_challenges,
        )
        summary["max_workers"] = self.max_workers
        self._update_report_parallelism(Path(self.output_dir), self.max_workers)
        return summary

    def _write_report(self, **kwargs: Any) -> dict[str, Any]:
        summary = helpers.write_benchmark_report(**kwargs)
        output_dir = Path(kwargs["output_dir"])
        driver = f"{self.provider}-baseline"
        for path in (
            output_dir / "results" / "evaluation_results.json",
            output_dir / "results" / "results.json",
        ):
            payload = _read_json(path)
            if not isinstance(payload, dict):
                continue
            if "summary" in payload and isinstance(payload["summary"], dict):
                payload["summary"]["driver"] = driver
            else:
                payload["driver"] = driver
            _write_json(path, payload)
        summary["driver"] = driver
        return summary

    def _update_report_parallelism(self, output_dir: Path, max_workers: int) -> None:
        for path in (
            output_dir / "results" / "evaluation_results.json",
            output_dir / "results" / "results.json",
        ):
            payload = _read_json(path)
            if not isinstance(payload, dict):
                continue
            if isinstance(payload.get("summary"), dict):
                payload["summary"]["max_workers"] = max_workers
            else:
                payload["max_workers"] = max_workers
            _write_json(path, payload)

    def _load_tasks(self) -> list[helpers.LoadedCybenchTask]:
        if self._selected_tasks_cache is None:
            task_list_path, tasks = helpers.load_cybench_tasks(
                cybench_dir=self._cybench_dir,
                task_list=self.task_list,
                challenge_name=self.challenge_name,
                max_challenges=self.max_challenges,
                easy_prompt=self.easy_prompt,
            )
            if not tasks:
                raise RuntimeError("No CyBench tasks selected.")
            self._task_list_path = task_list_path
            self._selected_tasks_cache = tasks
        return self._selected_tasks_cache

    def _run_one(
        self, task: helpers.LoadedCybenchTask, output_dir: Path
    ) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        sandbox_dir = output_dir / "sandbox_output"
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        staged_dir = Path(tempfile.mkdtemp(prefix=f"cybench_{task.canonical_name}_"))
        started_service = None
        serve_started = not task.has_service
        serve_health_checked = not task.has_service
        prompt = helpers.build_task_prompt(task)
        error: dict[str, Any] | None = None
        try:
            deadline = time.time() + self._time_limit_seconds
            generated_prompt = helpers.stage_cybench_task(
                task,
                staged_dir,
                easy_prompt=self.easy_prompt,
                script_timeout=self.stage_script_timeout,
                deadline=deadline,
            )
            prompt = helpers.build_task_prompt(
                task,
                generated_prompt=generated_prompt,
            )
            (sandbox_dir / "prompt.txt").write_text(prompt)
            if self.execution_mode == "docker" or task.has_service:
                helpers.ensure_docker_network(self.network_name)
            if task.has_service and not self.skip_services:
                started_service = helpers.start_cybench_service(
                    task,
                    network_name=self.network_name,
                    compose_output_dir=output_dir / "compose",
                    remove_host_ports=self.remove_host_ports,
                    rebuild_images=self.rebuild_service_images,
                    startup_deadline=deadline,
                )
                serve_started = bool(started_service and started_service.serve_started)
                serve_health_checked = bool(
                    started_service and started_service.health_checked
                )
            result = self._invoke_agent(
                prompt_path=sandbox_dir / "prompt.txt",
                staged_dir=staged_dir,
                sandbox_dir=sandbox_dir,
                network_name=self.network_name,
                timeout_seconds=max(1, int(deadline - time.time())),
                task_name=task.canonical_name,
            )
        except Exception as exc:
            logger.exception(
                "CyBench %s baseline failed for %s", self.provider, task.canonical_name
            )
            error = {"type": type(exc).__name__, "message": str(exc)}
            now = _utcnow_iso()
            result = ProcessResult(
                exit_code=1,
                exit_reason="timeout"
                if isinstance(exc, TimeoutError)
                else "task_error",
                timed_out=isinstance(exc, TimeoutError),
                budget_exhausted=False,
                budget=self._new_budget_tracker().snapshot(),
                started_at=now,
                finished_at=now,
                duration_seconds=0.0,
            )
        finally:
            shutil.rmtree(staged_dir, ignore_errors=True)

        session_trace = jsonl_to_session_trace(
            sandbox_dir / self._stream_filename(), provider=self.provider
        )
        helpers.write_raw_run_artifacts(
            output_dir=output_dir,
            sandbox_dir=sandbox_dir,
            prompt=prompt,
            session_id=None,
            session_trace=session_trace,
            exit_reason=result.exit_reason,
            started_at=result.started_at,
            finished_at=result.finished_at,
            duration_seconds=result.duration_seconds,
            serve_started=serve_started,
            serve_health_checked=serve_health_checked,
            agent_started=result.exit_reason != "task_error",
            time_limit_seconds=self._time_limit_seconds,
            budget_usd=self.budget,
            error=error,
        )
        self._write_baseline_artifacts(
            output_dir=output_dir,
            sandbox_dir=sandbox_dir,
            prompt=prompt,
            session_trace=session_trace,
            result=result,
            error=error,
        )
        helpers.score_task(
            output_dir=output_dir,
            task=task,
            prompt=prompt,
        )
        return self._augment_score(output_dir / "score.json", result)

    def _invoke_agent(
        self,
        *,
        prompt_path: Path,
        staged_dir: Path,
        sandbox_dir: Path,
        network_name: str,
        timeout_seconds: int,
        task_name: str,
    ) -> ProcessResult:
        tracker = self._new_budget_tracker()
        stdout_path = sandbox_dir / self._stream_filename()
        stderr_path = sandbox_dir / self._stderr_filename()
        if self.execution_mode == "docker":
            container_name = (
                f"cybench-{self.provider}-"
                f"{helpers.docker_safe_name(task_name, max_length=32)}-"
                f"{uuid.uuid4().hex[:8]}"
            )
            if self.provider == "codex":
                subprocess.run(
                    self._docker_create_command(
                        container_name=container_name,
                        prompt_path=Path("/workspace") / prompt_path.name,
                        staged_dir=staged_dir,
                        sandbox_dir=sandbox_dir,
                        network_name=network_name,
                    ),
                    check=True,
                    stdout=subprocess.DEVNULL,
                )
                try:
                    self._copy_codex_config_to_container(container_name)
                except Exception:
                    subprocess.run(
                        ["docker", "rm", "-f", container_name],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    raise
                cmd = ["docker", "start", "-a", container_name]
            else:
                cmd = self._docker_command(
                    container_name=container_name,
                    prompt_path=Path("/workspace") / prompt_path.name,
                    staged_dir=staged_dir,
                    sandbox_dir=sandbox_dir,
                    network_name=network_name,
                )
            kill_callback = lambda: subprocess.run(
                ["docker", "kill", container_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            cwd = None
        else:
            cmd = [
                "bash",
                "-c",
                self._agent_shell_command(
                    prompt_path=prompt_path.resolve(),
                    shared_dir=staged_dir.resolve(),
                    workspace_dir=sandbox_dir.resolve(),
                ),
            ]
            kill_callback = None
            cwd = sandbox_dir.resolve()
        try:
            result = _run_process(
                cmd,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                budget_tracker=tracker,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                kill_callback=kill_callback,
            )
        finally:
            if self.execution_mode == "docker" and self.provider == "codex":
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        if self.execution_mode == "docker":
            self._chown_workspace_to_host(sandbox_dir)
        return result

    def _docker_command(
        self,
        *,
        container_name: str,
        prompt_path: Path,
        staged_dir: Path,
        sandbox_dir: Path,
        network_name: str,
    ) -> list[str]:
        cmd = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            network_name,
            "-v",
            f"{staged_dir.resolve()}:/shared:ro",
            "-v",
            f"{sandbox_dir.resolve()}:/workspace",
            "-w",
            "/workspace",
        ]
        cmd.extend(self._env_file_args())
        cmd.extend(
            [
                self.image_tag,
                "bash",
                "-lc",
                self._agent_shell_command(
                    prompt_path=prompt_path,
                    shared_dir=Path("/shared"),
                    workspace_dir=Path("/workspace"),
                ),
            ]
        )
        return cmd

    def _docker_create_command(
        self,
        *,
        container_name: str,
        prompt_path: Path,
        staged_dir: Path,
        sandbox_dir: Path,
        network_name: str,
    ) -> list[str]:
        cmd = [
            "docker",
            "create",
            "--name",
            container_name,
            "--network",
            network_name,
            "-v",
            f"{staged_dir.resolve()}:/shared:ro",
            "-v",
            f"{sandbox_dir.resolve()}:/workspace",
            "-w",
            "/workspace",
        ]
        cmd.extend(self._env_file_args())
        cmd.extend(
            [
                self.image_tag,
                "bash",
                "-lc",
                self._agent_shell_command(
                    prompt_path=prompt_path,
                    shared_dir=Path("/shared"),
                    workspace_dir=Path("/workspace"),
                ),
            ]
        )
        return cmd

    def _agent_shell_command(
        self, *, prompt_path: Path, shared_dir: Path, workspace_dir: Path
    ) -> str:
        prompt = _quote(prompt_path)
        shared = _quote(shared_dir)
        workspace = _quote(workspace_dir)
        if self.provider == "claude":
            return (
                "set -o pipefail; "
                "claude --print "
                f"--model {_quote(self.model)} "
                f"--effort {_quote(self.reasoning_effort)} "
                f"--max-budget-usd {_quote(str(self.budget))} "
                "--dangerously-skip-permissions "
                f"--permission-mode {_quote(self.permission_mode)} "
                "--output-format stream-json --verbose "
                f"--add-dir {shared} "
                f"< {prompt}"
            )
        return (
            "set -o pipefail; "
            "codex exec --json "
            f"--model {_quote(self.model)} "
            f"--config model_reasoning_effort={_quote(json.dumps(self.reasoning_effort))} "
            "--sandbox danger-full-access "
            "--dangerously-bypass-approvals-and-sandbox "
            "--skip-git-repo-check "
            f"--output-last-message {workspace}/codex_final.txt "
            f"-C {workspace} --add-dir {shared} "
            f"- < {prompt}"
        )

    def _env_file_args(self) -> list[str]:
        if not self.env_file:
            return []
        env_file = Path(self.env_file).expanduser().resolve()
        if not env_file.exists():
            raise FileNotFoundError(
                f"Missing {env_file}. Copy {_image_dir(self.provider) / '.env.template'} "
                "to .env, or pass --env_file ''."
            )
        return ["--env-file", str(env_file)]

    def _codex_config_files(self) -> dict[str, Path]:
        if self.provider != "codex":
            return {}
        files: dict[str, Path] = {}
        for filename in ("auth.json", "config.toml"):
            path = _image_dir(self.provider) / filename
            if not path.exists():
                raise FileNotFoundError(
                    f"Missing {path}. Copy "
                    f"{_image_dir(self.provider) / (filename + '.template')} "
                    f"to {path.name} before running the Codex Docker baseline."
                )
            files[filename] = path.resolve()
        return files

    def _copy_codex_config_to_container(self, container_name: str) -> None:
        for filename, path in self._codex_config_files().items():
            subprocess.run(
                [
                    "docker",
                    "cp",
                    str(path),
                    f"{container_name}:/root/.codex/{filename}",
                ],
                check=True,
            )

    def _new_budget_tracker(self) -> BudgetTracker:
        return BudgetTracker(
            budget_usd=self.budget,
            input_cost_per_million=self.input_cost_per_million,
            cached_input_cost_per_million=self.cached_input_cost_per_million,
            output_cost_per_million=self.output_cost_per_million,
            reasoning_output_cost_per_million=self.reasoning_output_cost_per_million,
        )

    def _stream_filename(self) -> str:
        return (
            "claude_stream.jsonl" if self.provider == "claude" else "codex_events.jsonl"
        )

    def _stderr_filename(self) -> str:
        return "claude_stderr.log" if self.provider == "claude" else "codex_stderr.log"

    def _baseline_config(self) -> dict[str, Any]:
        return {
            "benchmark": "cybench",
            "driver": f"{self.provider}-baseline",
            "provider": self.provider,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "budget_usd": self.budget,
            "time_limit_seconds": self._time_limit_seconds,
            "max_workers": self.max_workers,
            "execution_mode": self.execution_mode,
            "image_tag": self.image_tag if self.execution_mode == "docker" else None,
            "image_dir": str(_image_dir(self.provider))
            if self.execution_mode == "docker"
            else None,
        }

    def _write_baseline_artifacts(
        self,
        *,
        output_dir: Path,
        sandbox_dir: Path,
        prompt: str,
        session_trace: dict[str, Any],
        result: ProcessResult,
        error: dict[str, Any] | None,
    ) -> None:
        config = self._baseline_config()
        budget = result.budget.to_dict()
        _write_json(output_dir / "baseline_config.json", config)
        _write_json(output_dir / "budget.json", budget)
        _write_json(
            output_dir / f"{self.provider}_exit_code.json",
            {"exit_code": result.exit_code},
        )
        _write_json(output_dir / "session_trace.json", session_trace)
        raw_dir = output_dir / "raw"
        raw_metadata = _read_json(raw_dir / "run_metadata.json") or {}
        raw_metadata.update(
            {
                **config,
                "budget": budget,
                "budget_spent_usd": budget["spent_usd"],
                "budget_source": budget["source"],
                "budget_is_estimate": budget["is_estimate"],
                "budget_exhausted": budget["budget_exhausted"],
            }
        )
        if error:
            raw_metadata["error"] = error
        _write_json(raw_dir / "run_metadata.json", raw_metadata)
        _write_json(raw_dir / "baseline_config.json", config)
        _write_json(raw_dir / "budget.json", budget)
        _write_json(
            raw_dir / f"{self.provider}_exit_code.json", {"exit_code": result.exit_code}
        )
        if sandbox_dir.exists() and not (raw_dir / "workspace").exists():
            _copy_path(sandbox_dir, raw_dir / "workspace")

    def _augment_score(self, score_path: Path, result: ProcessResult) -> dict[str, Any]:
        score = _read_json(score_path)
        if not isinstance(score, dict):
            score = {}
        budget = result.budget.to_dict()
        if result.exit_reason in {
            "agent_error",
            "budget_exhausted",
            "task_error",
            "timeout",
        }:
            score["exit_reason"] = result.exit_reason
        if budget["budget_exhausted"] and result.exit_reason != "timeout":
            score["exit_reason"] = "budget_exhausted"
        _write_json(score_path, score)
        return score

    def _chown_workspace_to_host(self, sandbox_dir: Path) -> None:
        if not hasattr(os, "getuid") or os.getuid() == 0:
            return
        subprocess.run(
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
                f"{os.getuid()}:{os.getgid()}",
                "/workspace",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


if __name__ == "__main__":
    import fire

    fire.Fire(CyBenchBaseline)
