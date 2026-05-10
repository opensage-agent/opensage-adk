"""Fake-user (user-simulator) support for multi-turn agent interactions.

After each agent invocation completes, a user-provided callback inspects the
Session and decides whether to inject a follow-up user message (returning a
str) or stop (returning None).

Protocol
--------
The callback signature is::

    async def my_fake_user(session: Session) -> str | None:
        ...

- Return ``str`` → framework injects it as the next user message and starts
  a new invocation.
- Return ``None`` → stop, no more follow-ups.

Users are free to implement any logic inside the callback: call an LLM,
run analysis code, read session.state, inspect session.events, or simply
return a constant string.

Useful Session fields
---------------------
- ``session.events`` — full event history; each Event has an
  ``invocation_id`` field, so you can group events by invocation to see
  what happened in the latest turn.
- ``session.state`` — agent state dict (e.g.
  ``session.state.get("task_finished")``).
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.agents.run_config import RunConfig
from google.adk.sessions import Session

if TYPE_CHECKING:
    from .manager import AgentManager

logger = logging.getLogger(__name__)


# ── Protocol ─────────────────────────────────────────────────────

FakeUserFn = Callable[[Session], Awaitable[Optional[str]]]
"""Callback signature: ``async (Session) -> str | None``."""


# ── Result ───────────────────────────────────────────────────────


@dataclass
class FakeUserRunResult:
    """Returned by :func:`run_with_fake_user`."""

    session: Session | None
    """Final session snapshot (may be None if no invocation ran)."""

    termination_reason: str
    """Why the loop stopped.

    One of: ``"fake_user_stop"``, ``"budget_exhausted"``,
    ``"max_turns"``.
    """

    events: list[Any] = field(default_factory=list)
    """All events from all invocations, in order.

    Collected in real time by the driver.  Unlike ``session.events``
    this list is never truncated by summarization / compaction.
    """

    llm_calls_used: int = 0
    """Total LLM calls consumed across all invocations."""


# ── Default implementation ───────────────────────────────────────

_DEFAULT_CONTINUATION_PROMPT = (
    "I approve you to continue, if you think the task is "
    "complete, you should call the finish_task tool, and "
    "then summarize the task and the result without calling "
    "any other tool. If you haven't submitted a poc that "
    "triggers the vulnerability, the task is not finished, "
    "continue and try harder, do not respond to this message "
    "in natural language, start calling appropriate tools to "
    "complete the task. DO NOT respond to this message."
)


async def default_fake_user(session: Session) -> str | None:
    """Default fake-user: continue until ``session.state["task_finished"]`` is True.

    This is the built-in implementation used when
    ``Evaluation.run_until_explicit_finish`` is True and no custom
    ``fake_user_fn`` is provided.

    .. note::

        The agent MUST have the ``finish_task`` tool in its toolset for
        ``session.state["task_finished"]`` to ever become True.  If the
        agent does not have this tool, the loop will only terminate when
        ``max_llm_calls`` or ``max_turns`` is reached.
    """
    if session and session.state:
        if session.state.get("task_finished", False):
            return None
    return _DEFAULT_CONTINUATION_PROMPT


# ── Loading from config ──────────────────────────────────────────


def load_fake_user_from_file(
    python_file: str,
    *,
    agent_dir: str | None = None,
) -> FakeUserFn:
    """Load a ``fake_user`` function from a user-supplied Python file.

    The file must define an async function named ``fake_user`` with
    signature ``async (Session) -> str | None``.

    Relative paths are resolved against ``agent_dir``.

    Raises:
        FileNotFoundError: file does not exist.
        ImportError: file failed to import or does not export ``fake_user``.
    """
    p = Path(python_file)
    if not p.is_absolute():
        if not agent_dir:
            raise ValueError(
                f"fake_user python_file is relative ({python_file!r}) but "
                f"agent_dir was not supplied; use an absolute path or "
                f"pass agent_dir."
            )
        p = (Path(agent_dir) / p).resolve()
    else:
        p = p.resolve()

    if not p.is_file():
        raise FileNotFoundError(f"fake_user python_file not found: {p}")

    spec = importlib.util.spec_from_file_location("opensage_fake_user", p)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module spec for {p}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise ImportError(f"Failed to import fake_user file {p}: {e}") from e

    fn = getattr(module, "fake_user", None)
    if fn is None or not callable(fn):
        raise ImportError(
            f"fake_user file {p} must export a callable named 'fake_user', "
            f"got {type(fn)}"
        )
    return fn


# ── Loop driver ──────────────────────────────────────────────────


async def run_with_fake_user(
    agent_manager: "AgentManager",
    session_id: str,
    first_message: str,
    fake_user: FakeUserFn,
    *,
    max_llm_calls: int | None = None,
    max_turns: int | None = None,
    event_callback: Callable[[Any], None] | None = None,
) -> FakeUserRunResult:
    """Drive a multi-turn agent conversation with a fake-user callback.

    After each invocation the callback receives the refreshed Session and
    decides whether to continue (return a str) or stop (return None).

    Args:
        agent_manager: AgentManager that owns the target instance.
        session_id: Session/instance to drive.
        first_message: User message for the first invocation.
        fake_user: The callback.  ``async (Session) -> str | None``.
        max_llm_calls: Total LLM-call budget.  ``None`` means unlimited.
            ``0`` is treated as unlimited for backward compatibility with
            ``Evaluation.max_llm_calls = 0``.
        max_turns: Maximum number of invocations (safety guard).
            ``None`` means no limit.
        event_callback: Called for each event as it streams (e.g. for
            writing a live trace file).  Receives one Event at a time.

    Returns:
        FakeUserRunResult with session snapshot, LLM usage, and the
        reason the loop stopped.
    """
    # Normalize: 0 means unlimited (backward compat)
    if max_llm_calls is not None and max_llm_calls <= 0:
        max_llm_calls = None

    session_service = agent_manager.session_service
    app_name = agent_manager.app_name
    user_id = agent_manager.default_user_id

    all_events: list[Any] = []
    remaining = max_llm_calls  # None = unlimited
    session_snapshot: Session | None = None
    termination_reason = "fake_user_stop"
    current_message = first_message
    turn = 0

    while True:
        # Max-turns guard
        if max_turns is not None and turn >= max_turns:
            termination_reason = "max_turns"
            break

        # Budget guard (check BEFORE invocation)
        if remaining is not None and remaining <= 0:
            termination_reason = "budget_exhausted"
            break

        # ── Run one invocation ──
        run_config = RunConfig(
            max_llm_calls=remaining if remaining is not None else 0,
        )

        try:
            async for event in agent_manager.run_turn_stream(
                session_id=session_id,
                request=current_message,
                run_config=run_config,
            ):
                all_events.append(event)
                if event_callback:
                    event_callback(event)
        except LlmCallsLimitExceededError as exc:
            logger.warning(
                "LLM-call budget exhausted for session %s: %s",
                session_id,
                exc,
            )
            termination_reason = "budget_exhausted"
            if max_llm_calls is not None:
                remaining = 0
            break

        # ── Post-invocation: refresh session, update budget ──
        session_snapshot = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )

        if remaining is not None and session_snapshot and session_snapshot.state:
            used_this_turn = int(
                session_snapshot.state.get("_adk", {}).get("llm_calls_used", 0) or 0
            )
            remaining = max(0, remaining - used_this_turn)

        # ── Ask the fake user ──
        next_message = await fake_user(session_snapshot)

        if next_message is None:
            termination_reason = "fake_user_stop"
            break

        current_message = next_message
        turn += 1

    # Final fallback: make sure we have a session snapshot
    if session_snapshot is None:
        session_snapshot = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )

    llm_calls_used = 0
    if max_llm_calls is not None and remaining is not None:
        llm_calls_used = max(0, max_llm_calls - remaining)

    return FakeUserRunResult(
        session=session_snapshot,
        termination_reason=termination_reason,
        events=all_events,
        llm_calls_used=llm_calls_used,
    )
