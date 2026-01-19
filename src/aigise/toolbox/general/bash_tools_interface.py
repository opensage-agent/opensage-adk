"""
Bash Tools Interface - Unified bash script invocation interface.

This module provides a unified interface for invoking scripts under
/sandbox_scripts/bash_tools and supports automatic discovery and registration
of these tools for agent use.
"""

from __future__ import annotations

import json
import logging
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from google.adk.tools.tool_context import ToolContext

from aigise.session import get_aigise_session
from aigise.toolbox.decorators import safe_tool_execution
from aigise.toolbox.general.bash_task_manager import BashTaskManager
from aigise.utils.agent_utils import (
    get_aigise_session_id_from_context,
    get_sandbox_from_context,
)
from aigise.utils.project_info import PROJECT_PATH

logger = logging.getLogger(__name__)

# Bash tools script directory
BASH_TOOLS_DIR = Path(PROJECT_PATH) / "src" / "aigise" / "bash_tools"
CONTAINER_BASH_TOOLS_DIR = "/bash_tools"


def _get_session(tool_context: ToolContext):
    """Return the active Aigise session for the provided tool context."""
    session_id = get_aigise_session_id_from_context(tool_context)
    return get_aigise_session(session_id)


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


class BashToolMetadata:
    """Metadata for bash tools, used to describe tool functionality and parameters."""

    def __init__(
        self,
        name: str,
        script_path: str,
        description: str,
        parameters: List[Dict[str, Any]],
        sandbox_types: List[str] = None,
        timeout: int = 60,
        returns_json: bool = False,
    ):
        """
        Args:
            name: Tool name (used to generate Python function name)
            script_path: Script path in container (relative to /sandbox_scripts/bash_tools)
            description: Tool description (for agent understanding)
            parameters: Parameter list, each parameter is a dict containing:
                - name: Parameter name
                - type: Parameter type (str, int, bool, etc.)
                - description: Parameter description
                - required: Whether required
                - default: Default value (optional)
            sandbox_types: List of required sandbox types, default ["main"]
            timeout: Timeout in seconds
            returns_json: Whether script returns JSON format
        """
        self.name = name
        self.script_path = script_path
        self.description = description
        self.parameters = parameters
        self.sandbox_types = sandbox_types or ["main"]
        self.timeout = timeout
        self.returns_json = returns_json

    def to_function_signature(self) -> Dict[str, Any]:
        """Convert to function signature for generating Python functions."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "sandbox_types": self.sandbox_types,
            "timeout": self.timeout,
            "returns_json": self.returns_json,
            "background": False,  # Default to False
        }


@safe_tool_execution
def run_bash_tool_script(
    script_name: str,
    args: Dict[str, Any],
    sandbox_type: str = "main",
    tool_context: Optional[ToolContext] = None,
    sandbox=None,  # Directly pass sandbox instance (for evaluation scenarios)
    timeout: int = 60,
    execution_timeout: Optional[int] = None,
    returns_json: bool = False,
    background: bool = False,
    param_definitions: Optional[
        List[Dict[str, Any]]
    ] = None,  # Parameter definitions (from metadata)
) -> Tuple[Any, int]:
    """Execute a registered bash tool script inside a sandbox.

    The function builds the command line from *args*, executes the
    corresponding `/bash_tools/<script_name>.sh` script, and optionally waits
    for completion.

    Args:
        script_name: Logical script identifier without extension ("find_git_repo").
        args: Mapping of parameter names to values used to build CLI arguments.
        sandbox_type: Sandbox to load from `tool_context` when `sandbox` is not
            provided explicitly.
        tool_context: Agent runtime context used to resolve sessions/sandboxes.
        sandbox: Pre-resolved sandbox instance (typically in evaluation flows).
        timeout: Seconds to wait for foreground completion before returning.
        execution_timeout: Hard timeout enforced on the command itself.
        returns_json: Whether to parse stdout as JSON when the exit code is 0.
        background: If True, return immediately after starting the task.
        param_definitions: Rich metadata describing positional/named arguments.

    Returns:
        Tuple[output, exit_code] where *output* is either raw stdout or parsed
        JSON when `returns_json` is True and parsing succeeds.

    Raises:
        ValueError: When neither `tool_context` nor `sandbox` is supplied.
    """
    # Prefer directly passed sandbox (for evaluation scenarios)
    if sandbox is None:
        if tool_context is None:
            raise ValueError(
                "Either tool_context or sandbox must be provided. "
                "Use tool_context for agent calls, or sandbox for evaluation code."
            )
        sandbox = get_sandbox_from_context(tool_context, sandbox_type)

    # Get TaskManager
    if tool_context:
        session = _get_session(tool_context)
        task_manager = _ensure_task_manager(session)
    else:
        # Evaluation scenario: persist manager directly on the sandbox object.
        task_manager = _ensure_task_manager(sandbox)

    # Build script path
    script_path = f"{CONTAINER_BASH_TOOLS_DIR}/{script_name}.sh"

    # Build command-line arguments
    cmd_parts = [script_path]

    # If parameter definitions exist, use smart parameter processing
    if param_definitions:
        # 1. Process positional parameters first (sorted by position)
        positional_params = [p for p in param_definitions if p.get("positional", False)]
        positional_params.sort(key=lambda p: p.get("position", 0))

        for param_def in positional_params:
            param_name = param_def["name"]
            if param_name in args and args[param_name] is not None:
                value = args[param_name]
                param_type = param_def.get("type", "str")

                # Handle list types (e.g., seed_paths, crash_names)
                if param_type == "list":
                    if isinstance(value, list):
                        for item in value:
                            cmd_parts.append(shlex.quote(str(item)))
                    elif value:  # Single value also treated as list
                        cmd_parts.append(shlex.quote(str(value)))
                else:
                    cmd_parts.append(shlex.quote(str(value)))

        # 2. Then process named parameters
        named_params = [p for p in param_definitions if not p.get("positional", False)]
        for param_def in named_params:
            param_name = param_def["name"]
            if param_name in args and args[param_name] is not None:
                value = args[param_name]
                param_type = param_def.get("type", "str")

                if param_type == "bool":
                    # Boolean type: only add flag when True
                    if value and value != "false" and value != "False":
                        cmd_parts.append(f"--{param_name}")
                else:
                    # Other types: --key value
                    cmd_parts.append(f"--{param_name}")
                    cmd_parts.append(shlex.quote(str(value)))
    else:
        # Legacy logic (compatibility): if no parameter definitions
        for key, value in args.items():
            if value is None:
                continue
            # If key is positional argument (e.g., _0, _1), add value directly
            if key.startswith("_"):
                cmd_parts.append(shlex.quote(str(value)))
            else:
                # Named arguments: --key value
                cmd_parts.append(f"--{key}")
                cmd_parts.append(shlex.quote(str(value)))

    command = " ".join(cmd_parts)

    logger.info(
        f"Bash tool {script_name} running command: {command} in sandbox {sandbox}"
    )

    # 1. Start as background task
    task_id, msg = task_manager.start_bg_task(
        sandbox, command, execution_timeout=execution_timeout
    )
    if not task_id:
        return msg, 1  # Error starting task

    # 2. If background requested, return immediately
    if background:
        return msg, 0

    # 3. If foreground, wait with timeout
    completed = task_manager.wait_for_task(sandbox, task_id, timeout)

    if completed:
        # Task finished, get output
        output = task_manager.get_task_output(sandbox, task_id)

        # Get exit code
        exit_code_val = task_manager.get_task_exit_code(sandbox, task_id)
        exit_code = exit_code_val if exit_code_val is not None else 1

        # Try to parse JSON if requested
        if returns_json:
            output = _parse_json_if_possible(output, context=f"{script_name} output")

        return output, exit_code
    else:
        # Timeout reached, task is still running in background
        return (
            f"Task timed out after {timeout}s. Continuing in background. Task ID: {task_id}",
            0,
        )


@safe_tool_execution
def list_available_scripts(
    start_dir: Optional[str] = None, *, tool_context: ToolContext
) -> str:
    """List available bash tools by printing full SKILL.md contents.

    Use this tool to discover what bash tools are available under `/bash_tools`
    and read their full documentation. For each discovered *executable* Skill
    (a directory containing `SKILL.md` and a `scripts/` subdirectory with at
    least one `.sh` or `.py` file), this returns the complete `SKILL.md`
    content.

    IMPORTANT (MUST FOLLOW):
    - Do NOT call this tool with the `/bash_tools` root directory (i.e.
      `start_dir="/bash_tools"`). That produces too much output.

    Args:
        start_dir: Optional subdirectory under bash_tools to start discovery from,
            e.g. "fuzz" or "static_analysis". If omitted, scans all bash_tools.
            DO NOT pass the `/bash_tools` root directory.

    Returns:
        str: Formatted list of available Skills with full SKILL.md content
    """

    def _normalize_start_dir(value: str) -> str:
        """Normalize start_dir into a relative path under BASH_TOOLS_DIR.

        Accepts both:
        - "retrieval"
        - "retrieval/search-symbol"
        - "/bash_tools/retrieval"
        - "/bash_tools/retrieval/search-symbol"
        """
        value = value.strip()
        # Treat "/bash_tools" (and "/bash_tools/" + extra trailing slashes) as root.
        if value.rstrip("/") == CONTAINER_BASH_TOOLS_DIR:
            return ""
        if value.startswith(f"{CONTAINER_BASH_TOOLS_DIR}/"):
            value = value[len(f"{CONTAINER_BASH_TOOLS_DIR}/") :]
        value = value.lstrip("/")
        # Disallow traversal.
        normalized = f"/{value}/"
        if value in (".", "..") or "/../" in normalized or "/./" in normalized:
            raise ValueError(f"Invalid start_dir: {value!r}")
        return value

    base_dir = BASH_TOOLS_DIR
    if start_dir:
        start_dir = _normalize_start_dir(start_dir)
        base_dir = (BASH_TOOLS_DIR / start_dir).resolve()
        if not base_dir.exists() or not base_dir.is_dir():
            return "Some unexpected error occurred. You should run ls or tree to explore the bash tools directory /bash_tools by your self."

    def _is_executable_skill_dir(skill_dir: Path) -> bool:
        if not (skill_dir / "SKILL.md").exists():
            return False
        scripts_dir = skill_dir / "scripts"
        if not scripts_dir.exists() or not scripts_dir.is_dir():
            return False
        script_files = list(scripts_dir.glob("*.sh")) + list(scripts_dir.glob("*.py"))
        return bool(script_files)

    skill_md_paths: list[Path] = []
    for skill_md in base_dir.rglob("SKILL.md"):
        if _is_executable_skill_dir(skill_md.parent):
            skill_md_paths.append(skill_md)

    if not skill_md_paths:
        return "No bash tools found in skills directories."

    root_label = "/bash_tools" if not start_dir else f"/bash_tools/{start_dir}"
    output = [f"Available Skills under {root_label}:", "=" * 30, ""]

    # Stable ordering by container path.
    def _container_path_for(skill_md: Path) -> str:
        rel_dir = skill_md.parent.relative_to(BASH_TOOLS_DIR)
        return f"{CONTAINER_BASH_TOOLS_DIR}/{rel_dir.as_posix()}/SKILL.md"

    for skill_md in sorted(skill_md_paths, key=_container_path_for):
        container_skill_md = _container_path_for(skill_md)
        output.append(f"--- {container_skill_md} ---")
        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception as exc:  # pylint: disable=broad-except
            output.append(f"ERROR: Failed to read SKILL.md: {exc}")
            output.append("")
            continue
        output.append(content.rstrip())
        output.append("")

    return "\\n".join(output)


@safe_tool_execution
def wait_for_background(
    task_id: str,
    timeout: int = 60,
    *,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Block until a background task finishes or the wait times out.

    Args:
        task_id: Identifier returned by `run_bash_tool_script` or
            `run_terminal_command` when the task was launched.
        timeout: Seconds to wait before returning with a timeout status.
        tool_context: Execution context used to locate session state.

    Returns:
        Dict with keys such as `success`, `output`, `exit_code`, and
        `status`. If the wait hits the timeout, `timeout=True` is included.
    """
    # Get TaskManager from session
    session = _get_session(tool_context)

    if not hasattr(session, "bash_tasks"):
        return {"success": False, "message": "No task manager found."}

    task_manager = session.bash_tasks

    if task_id not in task_manager.tasks:
        return {"success": False, "message": f"Task {task_id} not found."}

    task = task_manager.tasks[task_id]
    sandbox_name = task.get("sandbox_name", "main")

    try:
        sandbox = get_sandbox_from_context(tool_context, sandbox_name)
    except Exception as e:
        return {
            "success": False,
            "message": f"Could not access sandbox '{sandbox_name}': {str(e)}",
        }

    completed = task_manager.wait_for_task(sandbox, task_id, timeout)

    if completed:
        # Task finished, get output
        output = task_manager.get_task_output(sandbox, task_id)
        exit_code_val = task_manager.get_task_exit_code(sandbox, task_id)
        exit_code = exit_code_val if exit_code_val is not None else 1

        # Clean up task (memory + files)
        task_manager.cleanup_task(sandbox, task_id)

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


@safe_tool_execution
def run_terminal_command(
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
    scripts listed via `list_available_scripts` can be invoked.

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

    Args:
        command: The full command line to execute
            (e.g., "python3 -c 'print(123)' | cat").
        background: Whether to run the command in the background (default: False)
        timeout: Timeout in seconds for foreground commands, after which they will be moved to background
            (default: 60)
        execution_timeout: Timeout in seconds for the command itself, after which it will be terminated
            (default: None, meaning no timeout)
        sandbox_name: The name of the sandbox to run the command in
            (default: "main").
        tool_context: The tool context from the agent execution

    Returns:
        Dict describing execution status. When `background` is False the
        response contains `output` and `exit_code`. Otherwise, it returns the
        `task_id` needed to resume/inspect the background run.
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
    task_id, msg = task_manager.start_bg_task(
        sandbox,
        final_command,
        sandbox_name=target_sandbox,
        execution_timeout=execution_timeout,
    )
    if not task_id:
        return {"success": False, "error": msg}

    # 2. If background requested, return immediately
    if background:
        return {
            "success": True,
            "task_id": task_id,
            "message": "Command started in background. Use list_background_tasks and get_background_task_output to monitor.",
            "status": "running",
            "sandbox": target_sandbox,
        }

    # 3. If foreground, wait with timeout
    completed = task_manager.wait_for_task(sandbox, task_id, timeout)

    if completed:
        # Task finished, get output
        output = task_manager.get_task_output(sandbox, task_id)
        exit_code_val = task_manager.get_task_exit_code(sandbox, task_id)
        exit_code = exit_code_val if exit_code_val is not None else 1

        # Clean up task (memory + files)
        task_manager.cleanup_task(sandbox, task_id)

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
        # Timeout reached
        return {
            "success": True,
            "timeout": True,
            "message": f"Command timed out after {timeout}s but is still running in background.",
            "task_id": task_id,
            "status": "running",
            "sandbox": target_sandbox,
        }


@safe_tool_execution
def list_background_tasks(tool_context: ToolContext) -> Dict[str, Any]:
    """List all background tasks and their current status.

    This tool allows the agent to check the status of background tasks
    before making the next decision. It's particularly useful for:
    - Checking if fuzzing campaigns have completed
    - Monitoring long-running compilation or build processes
    - Verifying any task started with background=True parameter

    Args:
        tool_context: Tool context from the agent

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
    tasks = task_manager.list_tasks(sandbox_getter)

    if not tasks:
        return {"tasks": [], "summary": "No background tasks found."}

    # Generate summary
    status_counts = {}
    for task in tasks:
        status = task["status"]
        status_counts[status] = status_counts.get(status, 0) + 1

    summary_parts = [f"Total: {len(tasks)}"]
    for status in ["running", "completed", "failed", "completed/unknown"]:
        if status in status_counts:
            summary_parts.append(f"{status}: {status_counts[status]}")

    return {"tasks": tasks, "summary": ", ".join(summary_parts)}


@safe_tool_execution
def get_background_task_output(
    task_id: str, *, tool_context: ToolContext
) -> Dict[str, Any]:
    """Retrieve the output and exit code from a specific background task.

    Use this tool after launching a command with `background=True` or a
    command has been sent to the background. If the command already finished
    the helper returns the full logs and cleans up the underlying temp files;
    otherwise, it streams the current log buffer without interrupting the
    running process.

    Args:
        task_id: The ID of the task (from list_background_tasks)
        tool_context: Tool context from the agent

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

    task_manager = session.bash_tasks

    # Check if task exists
    if task_id not in task_manager.tasks:
        return {
            "error": f"Task {task_id} not found. Use list_background_tasks to see available tasks.",
            "task_id": task_id,
        }

    # Get task info
    task = task_manager.tasks[task_id]
    sandbox_name = task.get("sandbox_name", "main")

    try:
        sandbox = get_sandbox_from_context(tool_context, sandbox_name)
    except Exception as e:
        return {
            "error": f"Could not access sandbox '{sandbox_name}' for task {task_id}: {str(e)}",
            "task_id": task_id,
        }

    # Update status if still running
    if task["status"] == "running":
        # Trigger status update via list_tasks (efficient way to reuse logic?)
        # Or just check this single task manually
        # Let's reuse list_tasks for consistency, though it checks all
        def sandbox_getter(name):
            if name == sandbox_name:
                return sandbox
            return get_sandbox_from_context(tool_context, name)

        task_manager.list_tasks(sandbox_getter)
        task = task_manager.tasks[task_id]

    # Get output and exit code before cleanup
    output = task_manager.get_task_output(sandbox, task_id)
    exit_code = task_manager.get_task_exit_code(sandbox, task_id)

    # Prepare result
    result = {
        "task_id": task_id,
        "command": task["command"],
        "status": task["status"],
        "sandbox": sandbox_name,
        "output": output,
        "exit_code": exit_code if exit_code is not None else "unknown",
        "log_file": task["log_file"],
    }

    # Clean up: delete buffer files and remove from task management
    # Clean up ONLY if task is finished
    cleanup_success = False
    if task["status"] in ["completed", "failed", "killed", "completed/unknown"]:
        cleanup_success = task_manager.cleanup_task(sandbox, task_id)

    result["cleaned_up"] = cleanup_success

    return result


@safe_tool_execution
def kill_background_task(task_id: str, *, tool_context: ToolContext) -> Dict[str, Any]:
    """Kill a running background task.

    Args:
        task_id: The ID of the task to kill
        tool_context: Tool context from the agent

    Returns:
        dict: Result with 'success', 'message'
    """
    # Get TaskManager from session
    session = _get_session(tool_context)

    if not hasattr(session, "bash_tasks"):
        return {"success": False, "message": "No task manager found."}

    task_manager = session.bash_tasks

    if task_id not in task_manager.tasks:
        return {"success": False, "message": f"Task {task_id} not found."}

    task = task_manager.tasks[task_id]
    sandbox_name = task.get("sandbox_name", "main")

    try:
        sandbox = get_sandbox_from_context(tool_context, sandbox_name)
    except Exception as e:
        return {
            "success": False,
            "message": f"Could not access sandbox '{sandbox_name}': {str(e)}",
        }

    success = task_manager.kill_task(sandbox, task_id)

    if success:
        return {"success": True, "message": f"Task {task_id} killed."}
    else:
        return {"success": False, "message": f"Failed to kill task {task_id}."}
