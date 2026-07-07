"""
NeMo RL Environment for OpenSage.

Bridges NeMo RL's EnvironmentInterface with OpenSage's sandbox execution
and Harbor-compatible task verification.

NeMo RL controls the generation loop. This environment:
1. Parses tool calls from assistant responses
2. Executes them in OpenSage Docker sandboxes
3. Returns observations (tool output) for multi-turn
4. Runs verification (tests/test.sh) and returns reward on termination

Usage in NeMo RL config:
    env:
      opensage:
        tasks_dir: /path/to/harbor/tasks   # or harbor registry name
        max_turns: 30
        test_timeout: 120

Registration:
    from opensage.evaluation.rl_adapters.nemo_rl_env import OpenSageEnvironment
    nemo_rl.environments.utils.register_env(
        "opensage",
        "opensage.evaluation.rl_adapters.nemo_rl_env.OpenSageEnvironment"
    )
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, TypedDict

import docker
import ray
import torch

logger = logging.getLogger(__name__)

_TAG = "\033[1m[OpenSage]\033[0m"

# Harbor's default cache
HARBOR_TASKS_CACHE = Path.home() / ".cache" / "harbor" / "tasks"


class OpenSageEnvMetadata(TypedDict):
    task_id: str
    task_dir: str
    container_id: str | None
    turn: int
    max_turns: int
    test_timeout: int


class OpenSageEnvConfig(TypedDict):
    tasks_dir: str
    max_turns: int
    test_timeout: int
    docker_image_prefix: str


def _resolve_tasks_path(dataset_path: str) -> Path:
    """Resolve to local directory, download from harbor if needed."""
    local = Path(dataset_path)
    if local.is_dir():
        return local

    cached = HARBOR_TASKS_CACHE / dataset_path
    if cached.is_dir():
        return cached

    if shutil.which("harbor") is None:
        raise FileNotFoundError(
            f"'{dataset_path}' is not a local directory and `harbor` CLI is not installed."
        )

    logger.warning(f"Downloading harbor tasks: {dataset_path}")
    result = subprocess.run(
        ["harbor", "download", dataset_path, "-o", str(cached)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"harbor download failed:\n{result.stderr or result.stdout}")

    return cached


def _make_tool_parser(parser_name: str = "qwen3xml"):
    """Create a tool call parser using vLLM's Qwen3 XML parser.

    Currently only supports 'qwen3xml' (Qwen3.5 XML format).
    """
    if parser_name != "qwen3xml":
        raise ValueError(
            f"Unsupported tool_parser: '{parser_name}'. "
            f"Currently only 'qwen3xml' is supported."
        )

    from vllm.tool_parsers.qwen3xml_tool_parser import StreamingXMLToolCallParser

    def parse(content: str) -> list[dict]:
        if "<tool_call>" not in content:
            return []
        parser = StreamingXMLToolCallParser()
        result = parser.parse_single_streaming_chunks(content)
        calls = []
        for tc in result.tool_calls:
            if tc.function and tc.function.name:
                try:
                    args = (
                        json.loads(tc.function.arguments)
                        if tc.function.arguments
                        else {}
                    )
                except json.JSONDecodeError:
                    args = {"_raw": tc.function.arguments}
                calls.append({"name": tc.function.name, "arguments": args})
        return calls

    return parse


@ray.remote(max_restarts=-1, max_task_retries=-1)
class OpenSageEnvironment:
    """NeMo RL Environment using OpenSage sandboxes for tool execution.

    Implements the EnvironmentInterface contract:
    - step(): parse tool calls, execute in Docker, return observations
    - global_post_process_and_metrics(): aggregate metrics
    """

    def __init__(self, config: OpenSageEnvConfig):
        self.config = config
        self.tasks_dir = _resolve_tasks_path(config.get("tasks_dir", ""))
        self.max_turns = config.get("max_turns", 30)
        self.test_timeout = config.get("test_timeout", 120)
        self.docker_image_prefix = config.get("docker_image_prefix", "opensage_nemo")
        self._parse_tool_calls = _make_tool_parser(
            config.get("tool_parser", "qwen3xml")
        )
        self._trajectory_dir = None
        self._step_count = 0
        log_dir = config.get("log_dir")
        if log_dir:
            self._trajectory_dir = Path(log_dir) / "trajectories"
            self._trajectory_dir.mkdir(parents=True, exist_ok=True)
        self.docker_client = docker.from_env()

        logger.info(f"OpenSageEnvironment initialized: tasks_dir={self.tasks_dir}")

    def _get_or_create_container(self, metadata: OpenSageEnvMetadata) -> str:
        """Get existing container or create a new one for the task."""
        import time as _time

        if metadata.get("container_id"):
            return metadata["container_id"]

        task_id = metadata.get("task_id", "?")
        task_dir = Path(metadata["task_dir"])
        dockerfile_dir = task_dir / "environment"
        image_tag = f"{self.docker_image_prefix}_{metadata['task_id']}"

        # Build image if not exists
        try:
            self.docker_client.images.get(image_tag)
            print(
                f"{_TAG} task={task_id} | image {image_tag} exists, reusing", flush=True
            )
        except docker.errors.ImageNotFound:
            print(f"{_TAG} task={task_id} | building image {image_tag}...", flush=True)
            _t0 = _time.time()
            self.docker_client.images.build(
                path=str(dockerfile_dir),
                tag=image_tag,
                rm=True,
            )
            print(
                f"{_TAG} task={task_id} | image built in {_time.time() - _t0:.1f}s",
                flush=True,
            )

        # Create container
        container = self.docker_client.containers.run(
            image_tag,
            command="sleep infinity",
            detach=True,
            working_dir="/app",
        )
        print(
            f"{_TAG} task={task_id} | container {container.short_id} created",
            flush=True,
        )
        return container.id

    def _exec_in_container(
        self, container_id: str, command: str, timeout: int = 60
    ) -> str:
        """Execute command in container and return output."""
        import time as _time

        _t0 = _time.time()
        cmd_short = command[:80] + ("..." if len(command) > 80 else "")
        try:
            container = self.docker_client.containers.get(container_id)
            exit_code, output = container.exec_run(
                ["bash", "-c", command],
                demux=True,
            )
            stdout = output[0].decode("utf-8", errors="replace") if output[0] else ""
            stderr = output[1].decode("utf-8", errors="replace") if output[1] else ""
            result = stdout
            if stderr:
                result += f"\n[stderr]\n{stderr}"
            if exit_code != 0:
                result += f"\n[exit code: {exit_code}]"
            _elapsed = _time.time() - _t0
            print(
                f"{_TAG}   exec ({_elapsed:.1f}s, exit={exit_code}): {cmd_short}",
                flush=True,
            )
            return result[:10000]  # Truncate long output
        except Exception as e:
            _elapsed = _time.time() - _t0
            print(
                f"{_TAG}   exec FAILED ({_elapsed:.1f}s): {cmd_short} -> {e}",
                flush=True,
            )
            return f"[execution error: {e}]"

    def _run_verification(self, metadata: OpenSageEnvMetadata) -> tuple[bool, str]:
        """Run tests/test.sh in the container for final verification."""
        import time as _time

        task_id = metadata.get("task_id", "?")
        task_dir = Path(metadata["task_dir"])
        tests_dir = task_dir / "tests"
        container_id = metadata.get("container_id")

        if not container_id:
            print(
                f"{_TAG} task={task_id} | verification SKIPPED: no container",
                flush=True,
            )
            return False, "No container"

        if not tests_dir.exists():
            print(
                f"{_TAG} task={task_id} | verification SKIPPED: no tests/ directory",
                flush=True,
            )
            return False, "No tests/ directory"

        _t0 = _time.time()

        # Copy tests into container
        container = self.docker_client.containers.get(container_id)
        import io
        import tarfile

        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            for f in tests_dir.rglob("*"):
                if f.is_file():
                    tar.add(str(f), arcname=f"tests/{f.relative_to(tests_dir)}")
        tar_stream.seek(0)
        container.put_archive("/", tar_stream)

        # Run test.sh
        test_sh = tests_dir / "test.sh"
        if test_sh.exists():
            cmd = "chmod +x /tests/test.sh && /tests/test.sh"
        else:
            cmd = "cd /tests && python -m pytest -v --tb=short 2>&1"

        print(f"{_TAG} task={task_id} | running tests...", flush=True)
        output = self._exec_in_container(
            container_id, cmd, timeout=metadata.get("test_timeout", self.test_timeout)
        )

        # Check exit code from output
        passed = "[exit code:" not in output or "[exit code: 0]" in output
        _elapsed = _time.time() - _t0
        print(
            f"{_TAG} task={task_id} | verification {'PASSED' if passed else 'FAILED'} ({_elapsed:.1f}s)",
            flush=True,
        )
        return passed, output

    def _cleanup_container(self, container_id: str | None):
        """Remove container."""
        if not container_id:
            return
        try:
            container = self.docker_client.containers.get(container_id)
            container.remove(force=True)
        except Exception:
            pass

    def step(
        self,
        message_log_batch: list,
        metadata: list[OpenSageEnvMetadata | None],
    ):
        """Process assistant responses: execute tool calls, return observations."""
        from nemo_rl.environments.interfaces import EnvironmentReturn

        batch_size = len(message_log_batch)
        observations = []
        new_metadata = []
        rewards = torch.zeros(batch_size)
        terminateds = torch.zeros(batch_size, dtype=torch.bool)
        answers = [None] * batch_size

        import time as _time

        # Detect new batch: if any sample is on turn 0 (first call), increment step
        if any(m is not None and m.get("turn", 0) == 0 for m in metadata):
            self._step_count += 1

        for i in range(batch_size):
            meta = metadata[i]
            if meta is None:
                observations.append({"role": "environment", "content": ""})
                new_metadata.append(None)
                terminateds[i] = True
                continue

            _t0 = _time.time()
            turn = meta.get("turn", 0) + 1
            task_id = meta.get("task_id", "?")
            print(
                f"{_TAG} sample {i + 1}/{batch_size} | task={task_id} | turn={turn}",
                flush=True,
            )

            # Get or create container
            container_id = self._get_or_create_container(meta)
            meta["container_id"] = container_id
            meta["turn"] = turn

            # Extract last assistant message
            conversation = message_log_batch[i]
            last_assistant = ""
            for msg in reversed(conversation):
                if msg.get("role") == "assistant":
                    last_assistant = str(msg.get("content", ""))
                    break

            # Parse and execute tool calls
            tool_calls = self._parse_tool_calls(last_assistant)
            results = []
            tool_details = []  # For trajectory logging
            for call in tool_calls:
                name = call.get("name", "")
                args = call.get("arguments", {})
                output = ""
                if name in ("run_terminal_command", "bash", "shell"):
                    cmd = args.get("command", str(args))
                    output = self._exec_in_container(container_id, cmd)
                    results.append(f"$ {cmd}\n{output}")
                elif name in ("view_file", "read_file"):
                    path = args.get("path", args.get("file_path", ""))
                    output = self._exec_in_container(container_id, f"cat {path}")
                    results.append(output)
                elif name in ("str_replace_edit", "edit_file"):
                    # Write file edit
                    file_path = args.get("path", args.get("file_path", ""))
                    old = args.get("old_string", args.get("old_str", ""))
                    new = args.get("new_string", args.get("new_str", ""))
                    if old and new:
                        # Use python for reliable string replacement
                        py_cmd = (
                            f'python3 -c "'
                            f"p='{file_path}';"
                            f"t=open(p).read();"
                            f"open(p,'w').write(t.replace('''{old}''','''{new}''',1))\""
                        )
                        output = self._exec_in_container(container_id, py_cmd)
                        results.append(f"Edited {file_path}")
                    else:
                        output = f"Invalid edit args for {file_path}"
                        results.append(output)
                elif name == "finish_task":
                    # Agent signals completion
                    terminateds[i] = True
                    tool_details.append({"name": name, "arguments": args, "output": ""})
                    break
                tool_details.append({"name": name, "arguments": args, "output": output})

            if not tool_calls:
                results.append("No tool calls found in response.")

            tool_names = (
                [c.get("name", "?") for c in tool_calls] if tool_calls else ["none"]
            )
            obs_content = "\n".join(results) if results else "No output."

            # Check termination
            is_done = terminateds[i].item() or meta["turn"] >= meta.get(
                "max_turns", self.max_turns
            )

            if is_done:
                terminateds[i] = True
                # Run verification
                print(
                    f"{_TAG} sample {i + 1}/{batch_size} | task={task_id} | running verification...",
                    flush=True,
                )
                passed, test_output = self._run_verification(meta)
                rewards[i] = 1.0 if passed else 0.0
                obs_content += f"\n\n[Verification: {'PASSED' if passed else 'FAILED'}]"
                self._cleanup_container(container_id)
                new_metadata.append(None)
                _elapsed = _time.time() - _t0
                print(
                    f"{_TAG} sample {i + 1}/{batch_size} | task={task_id} | DONE reward={rewards[i]:.0f} | {_elapsed:.1f}s",
                    flush=True,
                )
            else:
                new_metadata.append(meta)
                _elapsed = _time.time() - _t0
                print(
                    f"{_TAG} sample {i + 1}/{batch_size} | task={task_id} | turn={turn} tools={tool_names} | {_elapsed:.1f}s",
                    flush=True,
                )

            observations.append({"role": "environment", "content": obs_content})

            # Write trajectory per task/sample
            if self._trajectory_dir:
                entry: dict = {
                    "task_id": task_id,
                    "sample": i,
                    "turn": turn,
                    "terminated": bool(terminateds[i]),
                    "reward": float(rewards[i]) if terminateds[i] else None,
                    "tool_calls": tool_details,
                    "elapsed": round(_elapsed, 1),
                    "assistant_response": last_assistant,
                    "env_observation": obs_content,
                }
                # On first turn, include the initial prompt for context
                if turn == 1:
                    prompt_messages = []
                    for msg in conversation:
                        role = msg.get("role", "")
                        if role == "assistant":
                            break
                        prompt_messages.append(
                            {"role": role, "content": msg.get("content", "")}
                        )
                    entry["prompt"] = prompt_messages
                step_dir = self._trajectory_dir / f"step_{self._step_count}" / task_id
                step_dir.mkdir(parents=True, exist_ok=True)
                sample_file = step_dir / f"sample_{i}.jsonl"
                with open(sample_file, "a") as f:
                    f.write(json.dumps(entry) + "\n")

        return EnvironmentReturn(
            observations=observations,
            metadata=new_metadata,
            next_stop_strings=[None] * batch_size,
            rewards=rewards,
            terminateds=terminateds,
            answers=answers,
        )

    def global_post_process_and_metrics(self, batch):
        """Compute aggregate metrics."""
        rewards = batch["total_reward"]
        metrics = {
            "opensage/pass_rate": (rewards > 0).float().mean().item(),
            "opensage/mean_reward": rewards.mean().item(),
        }
        return batch, metrics
