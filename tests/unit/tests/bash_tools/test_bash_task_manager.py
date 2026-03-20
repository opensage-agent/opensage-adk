"""Unit tests for BashTaskManager."""

from __future__ import annotations

from unittest.mock import MagicMock

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
