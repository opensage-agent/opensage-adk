# `[fake_user]` Reference

Field reference for the `[fake_user]` section. This section configures an automated user-simulator callback that drives multi-turn agent interactions.

## Overview

After each agent invocation completes, the fake-user function receives the current `Session` and decides whether to inject a follow-up user message (return a `str`) or stop (return `None`). This enables automated multi-turn conversations for benchmarks, testing, and unattended operation.

## Fields

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `python_file` | `string` | Path to a Python file exporting an `async def fake_user(session)` function | `None` |

## Path Resolution

- **Absolute paths** are used as-is.
- **Relative paths** are resolved against the agent directory (the `--agent` path).

## Python File Contract

The file specified by `python_file` must export an async function named `fake_user` with this signature:

```python
async def fake_user(session: Session) -> str | None:
    ...
```

**Input:** an ADK `Session` object containing:

- `session.events` — full event history. Each event has an `invocation_id` field for grouping by turn.
- `session.state` — agent state dict (e.g. `session.state.get("task_finished")`).

**Output:**

- Return `str` → the framework injects it as the next user message and starts a new invocation.
- Return `None` → stop, no more follow-ups.

## Example

```toml
# config.toml
[fake_user]
python_file = "my_fake_user.py"
```

```python
# my_fake_user.py
from google.adk.sessions import Session


async def fake_user(session: Session) -> str | None:
    """Continue until the agent signals completion."""
    if session.state.get("task_finished", False):
        return None
    return "Continue working on the task."
```

## Priority

When multiple sources can provide a fake-user function, they are checked in this order (first match wins):

1. `Evaluation.fake_user_fn` — programmatic override (e.g. from RL pipelines).
2. `config.fake_user.python_file` — the file specified in this TOML section.
3. `Evaluation.run_until_explicit_finish` — legacy field; uses the built-in `default_fake_user` function.
4. None — single invocation only, no follow-ups.

## Built-In Default

OpenSage ships a `default_fake_user` function (`opensage.orchestration.fake_user.default_fake_user`) that checks `session.state["task_finished"]` and sends a continuation prompt until it becomes `True`.

!!! warning
    The agent **must** have the `finish_task` tool in its toolset for `session.state["task_finished"]` to ever become `True`. Without it, the loop terminates only when `max_llm_calls` or `max_turns` is reached.

## Interaction with `run_until_explicit_finish`

Setting `python_file` takes precedence over `run_until_explicit_finish`. If both are configured, the function loaded from `python_file` is used and `run_until_explicit_finish` is ignored.

## Entry Points

The fake-user mechanism works in both entry points:

- **Evaluation (`_run_agent`)**: the loop is driven by `run_with_fake_user()`, which handles LLM budget tracking, event collection, and `LlmCallsLimitExceededError`.
- **CLI web (`opensage web`)**: after each invocation's SSE stream completes an iteration, the server calls `fake_user(session)` and continues streaming if a follow-up message is returned.
