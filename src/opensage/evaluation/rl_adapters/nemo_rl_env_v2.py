"""
NeMo RL Environment for OpenSage (v2 — ADK-integrated).

Uses OpenSage's ADK agent framework for tool execution instead of
manual Docker management. NeMo RL controls generation (vLLM), while
ADK handles system prompt, tool definitions, tool parsing, and
sandbox execution.

Architecture:
    NeMo RL (vLLM)
        │ generate text
        ▼
    nemo_rl_env.step(assistant_text)
        │ set_response(text)
        ▼
    ADK Agent (harbor_agent)
        ├── PassthroughLlm.generate_content_async()  ← returns buffered text
        ├── Tool call parsing (ADK framework)
        ├── Tool execution (OpenSage sandbox)
        └── Returns observation + tool results
        │
        ▼
    nemo_rl_env collects observation
        │
        ▼
    NeMo RL receives observation → next turn
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, TypedDict

import ray
import torch

logger = logging.getLogger(__name__)

_TAG = "\033[1m[OpenSage]\033[0m"


class OpenSageEnvMetadata(TypedDict):
    task_id: str
    task_dir: str
    turn: int
    max_turns: int
    test_timeout: int
    # ADK session state
    _session_id: str | None
    _agent_task: asyncio.Task | None
    _observation: str | None


class OpenSageEnvConfig(TypedDict):
    tasks_dir: str
    max_turns: int
    test_timeout: int
    agent_dir: str
    log_dir: str


def _resolve_tasks_path(dataset_path: str) -> Path:
    """Resolve to local path, checking harbor cache."""
    local = Path(dataset_path)
    if local.is_dir():
        return local
    cached = Path.home() / ".cache" / "harbor" / "tasks" / dataset_path
    if cached.is_dir():
        return cached
    raise FileNotFoundError(
        f"'{dataset_path}' not found as local dir or in harbor cache (~/.cache/harbor/tasks/)"
    )


@ray.remote(max_restarts=-1, max_task_retries=-1)
class OpenSageEnvironment:
    """NeMo RL Environment using OpenSage ADK agent for tool execution.

    Each sample gets its own ADK agent session with:
    - harbor_agent's system prompt and tools
    - OpenSage sandbox for Docker execution
    - PassthroughLLM that returns NeMo RL's vLLM output
    """

    def __init__(self, config: OpenSageEnvConfig):
        self.config = config
        self.tasks_dir = _resolve_tasks_path(config.get("tasks_dir", ""))
        self.max_turns = config.get("max_turns", 30)
        self.test_timeout = config.get("test_timeout", 120)
        self.agent_dir = config.get("agent_dir", "")

        # Trajectory logging
        self._trajectory_dir = None
        self._step_count = 0
        log_dir = config.get("log_dir")
        if log_dir:
            self._trajectory_dir = Path(log_dir) / "trajectories"
            self._trajectory_dir.mkdir(parents=True, exist_ok=True)

        # Per-sample agent sessions (keyed by sample index within batch)
        self._sessions: dict[int, _AgentSession] = {}
        self._loop = asyncio.new_event_loop()

        logger.info(f"OpenSageEnvironment (v2/ADK) initialized: tasks_dir={self.tasks_dir}")

    def step(
        self,
        message_log_batch: list,
        metadata: list[OpenSageEnvMetadata | None],
    ):
        """Process assistant responses through ADK agent sessions."""
        from nemo_rl.environments.interfaces import EnvironmentReturn

        batch_size = len(message_log_batch)
        observations = []
        new_metadata = []
        rewards = torch.zeros(batch_size)
        terminateds = torch.zeros(batch_size, dtype=torch.bool)
        answers = [None] * batch_size

        # Detect new batch
        if any(m is not None and m.get("turn", 0) == 0 for m in metadata):
            self._step_count += 1
            self._sessions.clear()

        for i in range(batch_size):
            meta = metadata[i]
            if meta is None:
                observations.append({"role": "environment", "content": ""})
                new_metadata.append(None)
                terminateds[i] = True
                continue

            t0 = time.time()
            turn = meta.get("turn", 0) + 1
            task_id = meta.get("task_id", "?")
            print(f"{_TAG} sample {i+1}/{batch_size} | task={task_id} | turn={turn}", flush=True)

            # Extract last assistant message
            conversation = message_log_batch[i]
            last_assistant = ""
            for msg in reversed(conversation):
                if msg.get("role") == "assistant":
                    last_assistant = str(msg.get("content", ""))
                    break

            # Get or create agent session
            if i not in self._sessions:
                task_dir = meta.get("task_dir", "")
                first_user_msg = ""
                for msg in conversation:
                    if msg.get("role") == "user":
                        first_user_msg = str(msg.get("content", ""))
                        break

                session = _AgentSession(
                    task_id=task_id,
                    task_dir=task_dir,
                    agent_dir=self.agent_dir,
                    first_user_message=first_user_msg,
                    loop=self._loop,
                )
                self._sessions[i] = session

            session = self._sessions[i]

            # Feed assistant response to ADK agent, get observation
            obs_content, tool_details = session.process_assistant_response(
                last_assistant, self._loop
            )

            meta["turn"] = turn

            # Check termination
            is_done = turn >= meta.get("max_turns", self.max_turns) or session.is_finished

            if is_done:
                terminateds[i] = True
                if session.is_finished:
                    # Agent called finish_task — run verification
                    print(f"{_TAG} sample {i+1}/{batch_size} | task={task_id} | running verification...", flush=True)
                    passed = session.run_verification(self.test_timeout)
                    rewards[i] = 1.0 if passed else 0.0
                    obs_content += f"\n\n[Verification: {'PASSED' if passed else 'FAILED'}]"
                else:
                    rewards[i] = 0.0
                    obs_content += "\n\n[Max turns reached]"

                session.cleanup()
                new_metadata.append(None)
                elapsed = time.time() - t0
                print(f"{_TAG} sample {i+1}/{batch_size} | task={task_id} | DONE reward={rewards[i]:.0f} | {elapsed:.1f}s", flush=True)
            else:
                new_metadata.append(meta)
                elapsed = time.time() - t0
                tool_names = [t.get("name", "?") for t in tool_details] if tool_details else ["none"]
                print(f"{_TAG} sample {i+1}/{batch_size} | task={task_id} | turn={turn} tools={tool_names} | {elapsed:.1f}s", flush=True)

            observations.append({"role": "environment", "content": obs_content})

            # Write trajectory
            if self._trajectory_dir:
                entry: dict = {
                    "task_id": task_id,
                    "sample": i,
                    "turn": turn,
                    "terminated": bool(terminateds[i]),
                    "reward": float(rewards[i]) if terminateds[i] else None,
                    "tool_calls": tool_details,
                    "elapsed": round(elapsed, 1),
                    "assistant_response": last_assistant,
                    "env_observation": obs_content,
                }
                if turn == 1:
                    prompt_messages = []
                    for msg in conversation:
                        if msg.get("role") == "assistant":
                            break
                        prompt_messages.append({"role": msg.get("role", ""), "content": msg.get("content", "")})
                    entry["prompt"] = prompt_messages

                step_dir = self._trajectory_dir / f"step_{self._step_count}" / task_id
                step_dir.mkdir(parents=True, exist_ok=True)
                with open(step_dir / f"sample_{i}.jsonl", "a") as f:
                    f.write(json.dumps(entry) + "\n")

        return EnvironmentReturn(
            observations=observations,
            metadata=new_metadata,
            next_stop_strings=[None] * batch_size,
            rewards=rewards,
            terminateds=terminateds,
            answers=answers,
        )

    def global_post_process_and_metrics(self, *args, **kwargs):
        return {}, {}


class _AgentSession:
    """Manages a single ADK agent session for one sample.

    Handles: agent creation, sandbox setup, feeding responses,
    collecting observations.
    """

    def __init__(
        self,
        task_id: str,
        task_dir: str,
        agent_dir: str,
        first_user_message: str,
        loop: asyncio.AbstractEventLoop,
    ):
        self.task_id = task_id
        self.task_dir = task_dir
        self.is_finished = False
        self._observation_queue: list[str] = []
        self._tool_details: list[dict] = []

        # Import here to avoid import at module level
        from opensage.evaluation.rl_adapters.passthrough_llm import PassthroughLlm

        self.llm = PassthroughLlm()

        # Create agent using harbor_agent's mk_agent
        self._setup_agent(agent_dir, first_user_message, loop)

    def _setup_agent(self, agent_dir: str, first_user_message: str, loop: asyncio.AbstractEventLoop):
        """Set up ADK agent with PassthroughLLM and OpenSage session."""
        import importlib.util
        import sys
        import uuid

        from google.adk.runners import Runner
        from google.adk.sessions.in_memory_session_service import InMemorySessionService

        # Load harbor_agent dynamically
        if agent_dir:
            agent_module_path = Path(agent_dir) / "agent.py"
        else:
            from opensage.utils.project_info import PROJECT_PATH
            agent_module_path = PROJECT_PATH / "examples" / "agents" / "harbor_agent" / "agent.py"

        spec = importlib.util.spec_from_file_location("harbor_agent", str(agent_module_path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Create agent with our PassthroughLLM
        self._session_id = str(uuid.uuid4())
        agent = module.mk_agent(
            opensage_session_id=self._session_id,
            model=self.llm,
        )

        # Set up runner
        self._session_service = InMemorySessionService()
        self._runner = Runner(
            agent=agent,
            app_name="opensage_nemo",
            session_service=self._session_service,
        )

        self._user_id = "nemo_rl"
        session = self._session_service.create_session(
            app_name="opensage_nemo",
            user_id=self._user_id,
            session_id=self._session_id,
        )

        # Send initial user message to start the session
        from google.genai import types
        self._first_message = types.Content(
            role="user",
            parts=[types.Part(text=first_user_message)],
        )
        self._agent_started = False

    def process_assistant_response(
        self, assistant_text: str, loop: asyncio.AbstractEventLoop
    ) -> tuple[str, list[dict]]:
        """Feed assistant response to ADK agent, collect observation.

        Returns:
            (observation_text, tool_details)
        """
        self.llm.set_response(assistant_text)

        # Run one round of the agent
        observations = []
        tool_details = []

        try:
            if not self._agent_started:
                # First turn: send user message to start
                events = list(self._runner.run(
                    user_id=self._user_id,
                    session_id=self._session_id,
                    new_message=self._first_message,
                ))
                self._agent_started = True
            else:
                # Subsequent turns: ADK should continue from where it left off
                # The PassthroughLLM already has the response buffered
                from google.genai import types
                # Send empty continuation to trigger next LLM call
                events = list(self._runner.run(
                    user_id=self._user_id,
                    session_id=self._session_id,
                    new_message=types.Content(
                        role="user",
                        parts=[types.Part(text="")],
                    ),
                ))

            # Process events
            for event in events:
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.function_response:
                            name = part.function_response.name
                            response = part.function_response.response
                            if isinstance(response, dict):
                                output = response.get("result", str(response))
                            else:
                                output = str(response)
                            tool_details.append({
                                "name": name,
                                "arguments": {},
                                "output": output,
                            })
                            observations.append(f"[{name}]: {output}")

                            if name == "finish_task":
                                self.is_finished = True

                        elif part.text and event.author != "user":
                            # Agent's own text (thinking/reasoning)
                            pass

        except Exception as e:
            logger.exception(f"Agent error for task {self.task_id}: {e}")
            observations.append(f"[Agent error: {e}]")

        obs_text = "\n".join(observations) if observations else "No tool calls found in response."
        return obs_text, tool_details

    def run_verification(self, timeout: int) -> bool:
        """Run Harbor tests via the sandbox."""
        # TODO: Use OpenSage's HarborEvaluation._run_task_tests
        # For now, fall back to direct Docker execution
        task_dir = Path(self.task_dir)
        tests_dir = task_dir / "tests"
        if not tests_dir.exists():
            return False

        try:
            import docker
            import tarfile
            import io

            client = docker.from_env()
            image_tag = f"opensage_nemo_{self.task_id}"

            # Find running container for this task
            containers = client.containers.list(
                filters={"ancestor": image_tag}
            )
            if not containers:
                print(f"{_TAG} task={self.task_id} | no container found for verification", flush=True)
                return False

            container = containers[0]

            # Copy tests
            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode="w") as tar:
                for f in tests_dir.rglob("*"):
                    if f.is_file():
                        tar.add(str(f), arcname=f"tests/{f.relative_to(tests_dir)}")
            tar_stream.seek(0)
            container.put_archive("/", tar_stream)

            test_sh = tests_dir / "test.sh"
            cmd = "chmod +x /tests/test.sh && /tests/test.sh" if test_sh.exists() else "cd /tests && python -m pytest -v --tb=short 2>&1"

            exit_code, output = container.exec_run(["bash", "-c", cmd], demux=True)
            passed = exit_code == 0
            print(f"{_TAG} task={self.task_id} | verification {'PASSED' if passed else 'FAILED'}", flush=True)
            return passed

        except Exception as e:
            logger.exception(f"Verification error: {e}")
            return False

    def cleanup(self):
        """Clean up agent session and sandbox."""
        try:
            from opensage.session import cleanup_opensage_session
            cleanup_opensage_session(self._session_id)
        except Exception:
            pass
