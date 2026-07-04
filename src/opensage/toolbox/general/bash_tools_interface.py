"""
Bash Tools Interface - Unified bash script invocation interface.

This module provides a unified interface for invoking scripts under
/bash_tools and supports automatic discovery and registration
of these tools for agent use.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional

from google.adk.tools.tool_context import ToolContext

from opensage.session import get_opensage_session
from opensage.toolbox.general.bash_task_manager import BashTaskManager, TaskStatus
from opensage.utils.agent_utils import (
    get_opensage_session_id_from_context,
    get_sandbox_from_context,
)

logger = logging.getLogger(__name__)


def _get_session(tool_context: ToolContext):
    """Return the active OpenSage session for the provided tool context."""
    session_id = get_opensage_session_id_from_context(tool_context)
    return get_opensage_session(session_id)


def _ensure_task_manager(host: Any) -> BashTaskManager:
    """Attach a BashTaskManager to *host* (session or sandbox) if missing."""
    if not hasattr(host, "bash_tasks"):
        host.bash_tasks = BashTaskManager()
    return host.bash_tasks


def _parse_json_if_possible(output: str, *, context: str = "output") -> Any:
    """Attempt to parse JSON output while keeping the original text on failure."""
    if not isinstance(output, str):
        return output

    stripped_output = output.strip()
    if stripped_output.startswith(("{", "[")):
        try:
            return json.loads(stripped_output)
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse JSON %s: %s", context, stripped_output[:100]
            )
    return output


async def wait_for_background(
    task_id: str,
    timeout: int = 300,
    *,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Block until a background task finishes or the wait times out.

    Note: background tasks now automatically deliver results to the agent's
    inbox on completion, so blocking here is usually unnecessary. Prefer
    ending your turn and letting the inbox notification wake you.

    Args:
        task_id (str): Identifier returned by `run_terminal_command` when
            the task was launched.
        timeout (int): Seconds to wait before returning with a timeout status.
    Returns:
        Dict[str, Any]: Dict with keys such as `success`, `output`, `exit_code`, and
        `status`. If the wait hits the timeout, `timeout=True` is included.
    """
    # Get TaskManager from session
    session = _get_session(tool_context)

    if not hasattr(session, "bash_tasks"):
        return {"success": False, "message": "No task manager found."}

    task_manager: BashTaskManager = session.bash_tasks

    if task_id not in task_manager.tasks:
        return {"success": False, "message": f"Task {task_id} not found."}

    task = task_manager.tasks[task_id]
    sandbox_name = task.sandbox_name

    try:
        sandbox = get_sandbox_from_context(tool_context, sandbox_name)
    except Exception as e:
        return {
            "success": False,
            "message": f"Could not access sandbox '{sandbox_name}': {str(e)}",
        }

    completed = await task_manager.wait_for_task_async(sandbox, task_id, timeout)

    if completed:
        # Task finished, get output
        output = await task_manager.get_task_output_async(sandbox, task_id)
        exit_code_val = await task_manager.get_task_exit_code_async(sandbox, task_id)
        exit_code = exit_code_val if exit_code_val is not None else 1

        # Clean up task (memory + files)
        await task_manager.cleanup_task_async(sandbox, task_id)

        # Try to parse JSON if it looks like JSON
        parsed_output = _parse_json_if_possible(output, context=f"task {task_id}")

        return {
            "success": exit_code == 0,
            "output": parsed_output,
            "exit_code": exit_code,
            "task_id": task_id,
            "sandbox": sandbox_name,
        }
    else:
        # Timeout reached
        return {
            "success": True,
            "timeout": True,
            "message": f"Command timed out after {timeout}s but is still running in background.",
            "task_id": task_id,
            "status": "running",
            "sandbox": sandbox_name,
        }


async def run_terminal_command(
    command: str,
    background: bool = False,
    timeout: int = 60,
    execution_timeout: Optional[int] = None,
    sandbox_name: str = "main",
    *,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Execute arbitrary bash inside the specified sandbox.

    This behaves like a one-off terminal session: any bash syntax, pipes, or
    scripts under `/bash_tools/<skill>/scripts/` can be invoked.

    Command syntax and escaping rules (what the model should assume):
      - **Write `command` exactly as you would type it in bash.** Pipes (`|`),
        redirection (`>`, `2>&1`), chaining (`&&`, `;`), subshells (`$(...)`),
        and quoting all work normally.
      - **Do NOT wrap your command in `bash -c` or `bash -lc`.** The backend
        already executes your command via `bash` (and sources `/shared/bashrc`
        if present). Wrapping again usually adds unnecessary quoting/escaping
        pitfalls.
      - **No extra escaping is required by the backend.** The backend does NOT
        wrap your string into a fragile `bash -c '...'` one-liner; instead it
        writes your command verbatim into a temporary script and executes it
        with `bash`. This is newline-safe and preserves quotes as-is.
      - The command runs as a **non-interactive** process (no TTY, no persistent
        shell session). If you see output like
        `mesg: ttyname failed: Inappropriate ioctl for device`, it's a benign
        warning from shell init logic; the command can still succeed.
      - Stdout/stderr are captured and returned (and for `background=True`, you
        can retrieve them later via `get_background_task_output`).

    Background task completion notification:
      When ``background=True``, a completion watcher automatically monitors the
      task. When the task finishes, its output is delivered to the agent's inbox
      and the agent is woken up — no manual polling with
      ``list_background_tasks`` / ``get_background_task_output`` is needed.
      Those tools remain available for checking intermediate progress of still-
      running tasks.

    Args:
        command (str): The full command line to execute
            (e.g., "python3 -c 'print(123)' | cat").
        background (bool): Whether to run the command in the background
            (default: False). The agent is notified via inbox when the task
            completes.
        timeout (int): Timeout in seconds for foreground commands, after which
            they will be moved to background (default: 60)
        execution_timeout (Optional[int]): Timeout in seconds for the command
            itself, after which it will be terminated
            (default: None, meaning no timeout)
        sandbox_name (str): The name of the sandbox to run the command in
            (default: "main").
    Returns:
        Dict[str, Any]: Dict describing execution status. When `background` is
        False the response contains `output` and `exit_code`. Otherwise, it
        returns the `task_id`; the result will be delivered to the agent's
        inbox when the task finishes.
    """
    # Determine sandbox
    target_sandbox = sandbox_name
    final_command = command

    # Get sandbox
    try:
        sandbox = get_sandbox_from_context(tool_context, target_sandbox)
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to get sandbox '{target_sandbox}': because {str(e)}",
        }

    # Get TaskManager
    session = _get_session(tool_context)
    task_manager = _ensure_task_manager(session)

    logger.info(
        f"Running terminal command: {final_command} in sandbox {target_sandbox} (background={background})"
    )

    # Execute
    # 1. Start as background task
    # We pass target_sandbox as the name, even if we are running in main (as a fallback)
    # This might be confusing, but it keeps the intent.
    # Or should we update the name to "main"?
    # Let's keep the intent but note the fallback in logs if we could.
    task_id, msg = await asyncio.to_thread(
        task_manager.start_bg_task,
        sandbox,
        final_command,
        target_sandbox,
        execution_timeout,
    )
    if not task_id:
        return {"success": False, "error": msg}

    # 2. If background requested, start completion watcher and return
    if background:
        try:
            from opensage.toolbox.general.orchestration_tools import (
                _get_caller_sid,
                _get_manager,
            )

            manager = _get_manager(tool_context)
            caller_sid = _get_caller_sid(tool_context)
            task_manager.start_completion_watcher(
                task_id,
                sandbox,
                manager,
                caller_sid,
            )
        except Exception:
            logger.debug(
                "Could not start completion watcher for task %s; "
                "agent must poll manually",
                task_id,
            )
        return {
            "success": True,
            "task_id": task_id,
            "message": "Command started in background. You will be notified via inbox when it completes.",
            "status": "running",
            "sandbox": target_sandbox,
        }

    # 3. If foreground, wait with timeout
    completed = await task_manager.wait_for_task_async(sandbox, task_id, timeout)

    if completed:
        # Task finished, get output
        output = await task_manager.get_task_output_async(sandbox, task_id)
        exit_code_val = await task_manager.get_task_exit_code_async(sandbox, task_id)
        exit_code = exit_code_val if exit_code_val is not None else 1

        # Clean up task (memory + files)
        await task_manager.cleanup_task_async(sandbox, task_id)

        # Try to parse JSON if it looks like JSON
        parsed_output = _parse_json_if_possible(output, context=f"task {task_id}")

        return {
            "success": exit_code == 0,
            "output": parsed_output,
            "exit_code": exit_code,
            "task_id": task_id,
            "sandbox": target_sandbox,
        }
    else:
        # Timeout reached — start watcher so agent gets notified on completion
        try:
            from opensage.toolbox.general.orchestration_tools import (
                _get_caller_sid,
                _get_manager,
            )

            manager = _get_manager(tool_context)
            caller_sid = _get_caller_sid(tool_context)
            task_manager.start_completion_watcher(
                task_id,
                sandbox,
                manager,
                caller_sid,
            )
        except Exception:
            logger.debug(
                "Could not start completion watcher for timed-out task %s",
                task_id,
            )
        return {
            "success": True,
            "timeout": True,
            "message": f"Command timed out after {timeout}s but is still running in background. You will be notified via inbox when it completes.",
            "task_id": task_id,
            "status": "running",
            "sandbox": target_sandbox,
        }


async def list_background_tasks(tool_context: ToolContext) -> Dict[str, Any]:
    """List all background tasks and their current status.

    Note: completed background tasks automatically deliver their results to
    the agent's inbox, so explicit polling is usually unnecessary. This tool
    is useful for checking *intermediate* progress of still-running tasks.

    Args:
    Returns:
        dict: Dictionary containing:
            - tasks: List of task information dictionaries, each with:
                - id: Task ID
                - pid: Process ID
                - command: The command that was run
                - status: Current status (running/completed/failed/completed/unknown)
                - sandbox: The sandbox where the task is running
            - summary: Human-readable summary of task counts by status
    """
    # Get TaskManager from session
    session = _get_session(tool_context)

    if not hasattr(session, "bash_tasks"):
        return {"tasks": [], "summary": "No background tasks have been started yet."}

    task_manager = session.bash_tasks

    # Define sandbox getter
    def sandbox_getter(name: str):
        return get_sandbox_from_context(tool_context, name)

    # Get all tasks with updated status
    tasks = await asyncio.to_thread(task_manager.list_tasks, sandbox_getter)

    if not tasks:
        return {"tasks": [], "summary": "No background tasks found."}

    tasks = [task.to_dict() for task in tasks]

    # Generate summary
    status_counts = {}
    for task in tasks:
        status = task.get("status")
        status_counts[status] = status_counts.get(status, 0) + 1

    summary_parts = [f"Total: {len(tasks)}"]
    for status, count in status_counts.items():
        summary_parts.append(f"{status}: {count}")

    return {"tasks": tasks, "summary": ", ".join(summary_parts)}


async def get_background_task_output(
    task_id: str, *, tool_context: ToolContext
) -> Dict[str, Any]:
    """Retrieve the output and exit code from a specific background task.

    Note: completed tasks automatically deliver their results to the agent's
    inbox, so you normally do not need to call this. It is still useful for
    reading *partial* output from a task that is still running.

    Args:
        task_id (str): The ID of the task (from list_background_tasks)
    Returns:
        dict: Dictionary containing:
            - task_id: The task ID
            - status: Current status of the task
            - output: The output from the task
            - exit_code: The exit code (0 for success, non-zero for failure)
            - error: Error message if task not found
            - cleaned_up: Boolean indicating if cleanup was performed
    """
    # Get TaskManager from session
    session = _get_session(tool_context)

    if not hasattr(session, "bash_tasks"):
        return {
            "error": "No background tasks manager found. No tasks have been started.",
            "task_id": task_id,
        }

    task_manager: BashTaskManager = session.bash_tasks

    # Check if task exists
    if task_id not in task_manager.tasks:
        return {
            "error": f"Task {task_id} not found. Use list_background_tasks to see available tasks.",
            "task_id": task_id,
        }

    # Get task info
    task = task_manager.tasks[task_id]
    sandbox_name = task.sandbox_name

    try:
        sandbox = get_sandbox_from_context(tool_context, sandbox_name)
    except Exception as e:
        return {
            "error": f"Could not access sandbox '{sandbox_name}' for task {task_id}: {str(e)}",
            "task_id": task_id,
        }

    # Update status if still running
    if task.status == TaskStatus.RUNNING:
        # Trigger status update via list_tasks (efficient way to reuse logic?)
        # Or just check this single task manually
        # Let's reuse list_tasks for consistency, though it checks all
        def sandbox_getter(name):
            if name == sandbox_name:
                return sandbox
            return get_sandbox_from_context(tool_context, name)

        await asyncio.to_thread(task_manager.list_tasks, sandbox_getter)
        task = task_manager.tasks[task_id]

    # Get output and exit code before cleanup
    output = await task_manager.get_task_output_async(sandbox, task_id)
    exit_code = await task_manager.get_task_exit_code_async(sandbox, task_id)

    # Prepare result
    result = {
        "task_id": task_id,
        "command": task.command,
        "status": task.status.value,
        "sandbox": sandbox_name,
        "output": output,
        "exit_code": exit_code if exit_code is not None else "unknown",
        "log_file": task.log_file,
    }

    # Clean up: delete buffer files and remove from task management
    # Clean up ONLY if task is finished
    cleanup_success = False
    if task.status.to_be_cleaned_up():
        cleanup_success = await task_manager.cleanup_task_async(sandbox, task_id)

    result["cleaned_up"] = cleanup_success

    return result


async def kill_background_task(
    task_id: str, *, tool_context: ToolContext
) -> Dict[str, Any]:
    """Kill a running background task.

    Args:
        task_id (str): The ID of the task to kill
    Returns:
        dict: Result with 'success', 'message'
    """
    # Get TaskManager from session
    session = _get_session(tool_context)

    if not hasattr(session, "bash_tasks"):
        return {"success": False, "message": "No task manager found."}

    task_manager: BashTaskManager = session.bash_tasks

    if task_id not in task_manager.tasks:
        return {"success": False, "message": f"Task {task_id} not found."}

    task = task_manager.tasks[task_id]
    sandbox_name = task.sandbox_name

    try:
        sandbox = get_sandbox_from_context(tool_context, sandbox_name)
    except Exception as e:
        return {
            "success": False,
            "message": f"Could not access sandbox '{sandbox_name}': {str(e)}",
        }

    success = await asyncio.to_thread(task_manager.kill_task, sandbox, task_id)

    if success:
        return {"success": True, "message": f"Task {task_id} killed."}
    else:
        return {"success": False, "message": f"Failed to kill task {task_id}."}
