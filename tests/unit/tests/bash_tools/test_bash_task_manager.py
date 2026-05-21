"""Unit tests for BashTaskManager."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from opensage.toolbox.general.bash_task_manager import BashTaskManager


def test_start_bg_task_execution_timeout_wraps_bash_not_user_command():
    """Ensure execution_timeout wraps `bash cmd_file`, preserving bash semantics.

    Regression: If we prefix the *user* command with `timeout ...`, then commands
    that start with env var assignments (e.g. `FOO=bar cmd`) break because
    `timeout` treats `FOO=bar` as the executable name.
    """
    manager = BashTaskManager()
    sandbox = MagicMock()

    calls: list[object] = []

    def _run(cmd):
        calls.append(cmd)
        # First call writes scripts; second call runs wrapper and returns PID.
        if len(calls) == 1:
            return ("ok", 0)
        return ("12345\n", 0)

    sandbox.run_command_in_container = MagicMock(side_effect=_run)

    user_command = (
        "TARGET_BINARY=/out/wkb_import_fuzzer "
        "/bash_tools/coverage/run-coverage/scripts/run_coverage.sh /shared/poc"
    )
    task_id, _ = manager.start_bg_task(
        sandbox,
        user_command,
        sandbox_name="coverage",
        execution_timeout=600,
    )

    assert task_id is not None
    assert len(calls) >= 1

    write_files_cmd = calls[0]
    assert isinstance(write_files_cmd, str)

    # The command script should contain the user command verbatim, without timeout.
    assert user_command in write_files_cmd
    assert f"timeout -k 5 600 {user_command}" not in write_files_cmd

    # The wrapper should apply timeout to the bash invocation instead.
    assert "timeout -k 5 600 bash " in write_files_cmd


def test_active_watcher_task_ids_filters_by_owner():
    manager = BashTaskManager()
    manager.tasks = {
        "a": MagicMock(owner_session_id="root"),
        "b": MagicMock(owner_session_id="child"),
        "c": MagicMock(owner_session_id=None),
    }

    async def _never():
        await asyncio.Event().wait()

    loop = asyncio.new_event_loop()
    try:
        watcher_a = loop.create_task(_never())
        watcher_b = loop.create_task(_never())
        done = loop.create_task(asyncio.sleep(0))
        loop.run_until_complete(done)
        manager._watcher_tasks = {
            "a": watcher_a,
            "b": watcher_b,
            "c": done,
        }

        assert manager.get_active_watcher_task_ids(
            lambda owner_sid: owner_sid == "root"
        ) == ["a"]
        assert manager.get_active_watcher_task_ids(
            lambda owner_sid: owner_sid in {"root", "child"}
        ) == ["a", "b"]
    finally:
        watcher_a.cancel()
        watcher_b.cancel()
        loop.run_until_complete(
            asyncio.gather(watcher_a, watcher_b, return_exceptions=True)
        )
        loop.close()


@pytest.mark.asyncio
async def test_wait_for_watcher_tasks_timeout_does_not_cancel_watcher():
    manager = BashTaskManager()
    manager.tasks = {"task": MagicMock(owner_session_id="root")}
    watcher = asyncio.create_task(asyncio.sleep(10))
    manager._watcher_tasks = {"task": watcher}

    with pytest.raises(asyncio.TimeoutError):
        await manager.wait_for_watcher_tasks(["task"], timeout=0.01)

    assert not watcher.cancelled()
    watcher.cancel()
    await asyncio.gather(watcher, return_exceptions=True)
