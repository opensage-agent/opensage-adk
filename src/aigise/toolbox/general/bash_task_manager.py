import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Status of a background task."""

    RUNNING = "running"
    COMPLETED = "completed"
    KILLED = "killed"
    UNKNOWN = "unknown"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"

    def to_be_cleaned_up(self) -> bool:
        """Determine if the task is in a state suitable for cleanup."""
        return self in {
            TaskStatus.COMPLETED,
            TaskStatus.KILLED,
            TaskStatus.UNKNOWN,
            TaskStatus.SANDBOX_UNAVAILABLE,
        }


@dataclass
class Task:
    """Represents a background bash task."""

    id: str
    pid: str
    command: str
    sandbox_name: str
    log_file: str
    exit_code_file: str
    pid_file: str
    cmd_file: str
    wrapper_file: str
    status: TaskStatus = TaskStatus.RUNNING
    exit_code: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of this task."""
        status = self.status.value if hasattr(self.status, "value") else self.status
        return {
            "id": self.id,
            "pid": self.pid,
            "command": self.command,
            "status": status,
            "sandbox": self.sandbox_name,
            "log_file": self.log_file,
            "sandbox_name": self.sandbox_name,
            "exit_code_file": self.exit_code_file,
            "pid_file": self.pid_file,
            "cmd_file": self.cmd_file,
            "wrapper_file": self.wrapper_file,
            "exit_code": self.exit_code,
        }


class BashTaskManager:
    """Manages background bash tasks for a session."""

    def __init__(self):
        # Storage for tasks: task_id -> Task
        self.tasks: Dict[str, Task] = {}

    @staticmethod
    def _heredoc_delimiter(task_id: str, *, purpose: str) -> str:
        """Return a heredoc delimiter unlikely to appear in user content."""
        return f"__OPENSAGE_TASK_{task_id}_{purpose}__"

    def start_bg_task(
        self,
        sandbox,
        command: str,
        sandbox_name: str = "main",
        execution_timeout: Optional[int] = None,
    ) -> tuple[Optional[str], str]:
        """Start a command in the background.

        Args:
            sandbox: The sandbox instance to run the command in.
            command (str): The bash command to execute.
            sandbox_name (str): The name of the sandbox (e.g., "main", "fuzz").
        Returns:
            tuple[Optional[str], str]: Tuple of (task_id, message). If task_id is None, message is error.
        """
        task_id = str(uuid.uuid4())[:8]
        exit_code_file = f"/tmp/task_{task_id}.exit"
        log_file = f"/tmp/task_{task_id}.log"
        pid_file = f"/tmp/task_{task_id}.pid"
        cmd_file = f"/tmp/task_{task_id}.cmd.sh"
        wrapper_file = f"/tmp/task_{task_id}.wrapper.sh"

        # Instead of interpolating the user's command into a quoted `bash -c '...'`
        # string (which is very fragile for quotes/newlines), write the command to a
        # temp script file in the container and execute that script.
        cmd_delim = self._heredoc_delimiter(task_id, purpose="CMD")
        wrapper_delim = self._heredoc_delimiter(task_id, purpose="WRAPPER")

        # Write the user command verbatim into a script and run it with bash.
        #
        # Important: Do NOT prefix the user command with `timeout ...` here.
        # Prefixing changes bash semantics for leading env var assignments like:
        #   FOO=bar some_command ...
        # because `timeout` would treat `FOO=bar` as the program name.
        cmd_script = f"""#!/bin/bash
{command}
"""

        # Wrapper script responsibilities:
        # 1) Detach from parent using setsid.
        # 2) Source /shared/bashrc to load env vars.
        # 3) Run the command script in background, capture PID, wait, record exit code.
        timeout_prefix = (
            f"timeout -k 5 {execution_timeout} " if execution_timeout else ""
        )
        wrapper_script = f"""#!/bin/bash
set -euo pipefail

setsid bash -c '
  if [ -f /shared/bashrc ]; then
    source /shared/bashrc
  fi
  {timeout_prefix}bash {cmd_file} > {log_file} 2>&1 &
  COMMAND_PID=$!
  echo $COMMAND_PID > {pid_file}
  wait $COMMAND_PID
  echo $? > {exit_code_file}
' >/dev/null 2>&1 &

# Wait for PID file to be written (up to 2 seconds)
count=0
while [ ! -f {pid_file} ] && [ $count -lt 20 ]; do
  sleep 0.1
  count=$((count+1))
done

# Read and print the PID
if [ -f {pid_file} ]; then
  cat {pid_file}
else
  echo "ERROR: PID file not created" >&2
  exit 1
fi
"""

        write_files_cmd = (
            f"cat > {cmd_file} << '{cmd_delim}'\n{cmd_script}\n{cmd_delim}\n"
            f"chmod +x {cmd_file}\n"
            f"cat > {wrapper_file} << '{wrapper_delim}'\n{wrapper_script}\n{wrapper_delim}\n"
            f"chmod +x {wrapper_file}"
        )

        output, exit_code = sandbox.run_command_in_container(write_files_cmd)
        if exit_code != 0:
            return None, f"Failed to create task scripts: {output}"

        # Execute the wrapper script
        output, exit_code = sandbox.run_command_in_container(["bash", wrapper_file])

        logger.info(
            f"Background task scheduled using wait command with exit code: {exit_code}"
        )

        if exit_code != 0:
            return None, f"Failed to start background task: {output}"

        # Get the PID from the output (should be the first line)
        lines = output.strip().splitlines()
        if not lines or not lines[0].strip().isdigit():
            return None, f"Failed to get PID. Output: {output}"

        pid = lines[0].strip()

        task = Task(
            id=task_id,
            pid=pid,
            command=command,
            sandbox_name=sandbox_name,
            log_file=log_file,
            exit_code_file=exit_code_file,
            pid_file=pid_file,
            cmd_file=cmd_file,
            wrapper_file=wrapper_file,
            status=TaskStatus.RUNNING,
            exit_code=None,
        )
        self.tasks[task_id] = task

        return (
            task_id,
            f"Task started. ID: {task_id}, PID: {pid}, Log: {log_file} (Sandbox: {sandbox_name})",
        )

    def list_tasks(self, sandbox_getter) -> list[Task]:
        """List all tasks and update their status.

        Args:
            sandbox_getter: A function that takes a sandbox_name (str) and returns a sandbox instance."""
        active_tasks = []
        for task_id, task in self.tasks.items():
            if task.status == TaskStatus.RUNNING:
                sandbox_name = task.sandbox_name
                try:
                    sandbox = sandbox_getter(sandbox_name)
                except Exception as e:
                    logger.warning(
                        f"Could not get sandbox '{sandbox_name}' for task {task_id}: {e}"
                    )
                    task.status = TaskStatus.SANDBOX_UNAVAILABLE
                    active_tasks.append(task)
                    continue

                # Check if process is still running
                pid = task.pid
                check_cmd = f"kill -0 {pid}"
                _, exit_code = sandbox.run_command_in_container(check_cmd)

                if exit_code != 0:
                    # Process finished, check exit code file
                    exit_code_val = self.get_task_exit_code(sandbox, task_id)
                    if exit_code_val is not None:
                        task.exit_code = exit_code_val
                        task.status = TaskStatus.COMPLETED
                    else:
                        task.status = TaskStatus.UNKNOWN

            active_tasks.append(task)
        return active_tasks

    def get_task_output(self, sandbox, task_id: str) -> str:
        """Get the output log of a task."""
        if task_id not in self.tasks:
            return "Task not found"

        task = self.tasks[task_id]
        log_file = task.log_file
        output, _ = sandbox.run_command_in_container(f"cat {log_file}")
        return output

    def get_task_exit_code(self, sandbox, task_id: str) -> Optional[int]:
        """Get the exit code of a completed task."""
        if task_id not in self.tasks:
            return None

        exit_code_file = self.tasks[task_id].exit_code_file
        if not exit_code_file:
            return None

        output, exit_code = sandbox.run_command_in_container(f"cat {exit_code_file}")
        if exit_code == 0 and output.strip().isdigit():
            return int(output.strip())
        return None

    def wait_for_task(self, sandbox, task_id: str, timeout: int = 60) -> bool:
        """Wait for a task to complete.

        Args:
            sandbox: The sandbox instance.
            task_id (str): The ID of the task to wait for.
            timeout (int): Maximum time to wait in seconds.
        Returns:
            bool: True if task completed, False if timed out.
        """
        import time

        if task_id not in self.tasks:
            return False

        task = self.tasks[task_id]
        pid = task.pid
        start_time = time.time()

        while time.time() - start_time < timeout:
            # Check if process is still running
            check_cmd = f"kill -0 {pid}"
            _, exit_code = sandbox.run_command_in_container(check_cmd)

            if exit_code != 0:
                # Process finished
                exit_code_val = self.get_task_exit_code(sandbox, task_id)
                if exit_code_val is not None:
                    task.exit_code = exit_code_val
                    task.status = TaskStatus.COMPLETED
                else:
                    task.status = TaskStatus.UNKNOWN
                return True

            time.sleep(1)

        return False

    def cleanup_task(self, sandbox, task_id: str) -> bool:
        """Clean up a task by deleting temporary files and removing from management.

        This method should be called after consuming the task output to free up
        resources and prevent memory/disk leaks.

        Args:
            sandbox: The sandbox instance.
            task_id (str): The ID of the task to clean up.
        Returns:
            bool: True if cleanup was successful, False otherwise.
        """
        if task_id not in self.tasks:
            logger.warning(f"Cannot cleanup task {task_id}: task not found")
            return False

        task = self.tasks[task_id]

        # Delete temporary files from sandbox
        files_to_delete = [
            task.log_file,
            task.exit_code_file,
            task.pid_file,
            task.cmd_file,
            task.wrapper_file,
        ]

        cleanup_success = True
        for file_path in files_to_delete:
            if file_path:
                delete_cmd = f"rm -f {file_path}"
                _, exit_code = sandbox.run_command_in_container(delete_cmd)
                if exit_code != 0:
                    logger.warning(
                        f"Failed to delete file {file_path} for task {task_id}"
                    )
                    cleanup_success = False

        # Remove task from dictionary
        del self.tasks[task_id]
        logger.info(f"Task {task_id} cleaned up successfully")

        return cleanup_success

    def kill_task(self, sandbox, task_id: str) -> bool:
        """Kill a running task.

        Args:
            sandbox: The sandbox instance.
            task_id (str): The ID of the task to kill.
        Returns:
            bool: True if kill signal was sent successfully, False otherwise.
        """
        if task_id not in self.tasks:
            logger.warning(f"Cannot kill task {task_id}: task not found")
            return False

        task = self.tasks[task_id]
        pid = task.pid

        # Kill the process group to ensure children (and the wrapper/timeout) are killed
        # Using -TERM first, then could fallback to -9 if needed, but for now let's try strict kill
        # Note: In the wrapper script, we used `setsid`, so the process should be a group leader.
        # Passing negative PID to kill sends signal to the process group.
        kill_cmd = f"kill -9 -{pid} || kill -9 {pid}"

        output, exit_code = sandbox.run_command_in_container(kill_cmd)

        if exit_code == 0:
            task.status = TaskStatus.KILLED
            logger.info(f"Task {task_id} (PID {pid}) killed successfully")
            return True
        else:
            logger.warning(f"Failed to kill task {task_id} (PID {pid}): {output}")
            return False
