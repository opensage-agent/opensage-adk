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
    returns_json: bool = False,
    background: bool = False,
    param_definitions: Optional[
        List[Dict[str, Any]]
    ] = None,  # Parameter definitions (from metadata)
) -> Tuple[Any, int]:
    """
    Unified bash tool script invocation interface.

    Args:
        script_name: Script name without path and extension (e.g., "find_git_repo")
        args: Arguments dictionary that will be converted to command-line arguments
        sandbox_type: Sandbox type to use (when tool_context or sandbox is None)
        tool_context: Tool context (if called from agent)
        sandbox: Directly pass sandbox instance (for evaluation scenarios, takes priority over tool_context)
        timeout: Timeout in seconds
        returns_json: Whether to parse JSON return value
        background: Whether to run in background
        param_definitions: Parameter definitions list (from skill metadata parameters)

    Returns:
        (output, exit_code): Output and exit code
        If returns_json=True and exit_code=0, output will be parsed as dict/list
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
    task_manager = None
    if tool_context:
        session_id = get_aigise_session_id_from_context(tool_context)
        session = get_aigise_session(session_id)
        if not hasattr(session, "bash_tasks"):
            session.bash_tasks = BashTaskManager()
        task_manager = session.bash_tasks
    elif sandbox:
        # Fallback for direct sandbox usage (e.g. eval), create a temporary manager or handle differently
        # For now, we assume tool_context is available for session persistence,
        # or we create a local one if needed but it won't persist across calls without session.
        # In eval context, we might not need persistent background tasks as much, or we attach to sandbox?
        # Let's attach to sandbox object dynamically if needed.
        if not hasattr(sandbox, "bash_tasks"):
            sandbox.bash_tasks = BashTaskManager()
        task_manager = sandbox.bash_tasks

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
    task_id, msg = task_manager.start_bg_task(sandbox, command)
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
            try:
                output = json.loads(output.strip())
            except json.JSONDecodeError:
                logger.warning(
                    f"Failed to parse JSON output from {script_name}: {output[:100]}"
                )
                # Return original output

        return output, exit_code
    else:
        # Timeout reached, task is still running in background
        return (
            f"Task timed out after {timeout}s. Continuing in background. Task ID: {task_id}",
            0,
        )


def _parse_skill_md_config(content: str) -> Dict[str, Any]:
    """
    Parse configuration information from SKILL.md content.

    Extracts:
    - Parameters: Parse parameter definitions from ## Parameters section
    - Timeout: Parse from ## Timeout section
    - Returns JSON: Determine if returns JSON from ## Return Value section

    Args:
        content: SKILL.md file content

    Returns:
        Configuration dictionary containing parameters, sandbox_types, timeout, returns_json
    """
    import re

    config = {
        "parameters": [],
        "sandbox_types": ["main"],
        "timeout": 60,
        "returns_json": False,
    }

    # Parse Timeout
    timeout_match = re.search(
        r"## Timeout\s*\n\s*.*?(\d+)\s+seconds", content, re.IGNORECASE
    )
    if timeout_match:
        config["timeout"] = int(timeout_match.group(1))

    # Determine if returns JSON - check for both code blocks and text descriptions
    if re.search(
        r"## Return Value\s*\n.*?```json", content, re.DOTALL | re.IGNORECASE
    ) or re.search(
        r"## Return Value\s*\n.*?Returns.*JSON", content, re.DOTALL | re.IGNORECASE
    ):
        config["returns_json"] = True

    # Parse Parameters
    params_section_match = re.search(
        r"## Parameters\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL
    )
    if params_section_match:
        params_text = params_section_match.group(1)

        # Find all parameter definitions (### parameter_name format)
        param_blocks = re.finditer(r"### (\w+)\s*\(([^)]+)\)", params_text)

        position = 0
        for param_match in param_blocks:
            param_name = param_match.group(1)
            param_attrs = param_match.group(2)

            # Parse attributes: required/optional, positional position X
            is_required = "required" in param_attrs.lower()
            is_positional = "positional" in param_attrs.lower()

            # Extract position
            pos_match = re.search(r"position\s+(\d+)", param_attrs)
            if pos_match:
                param_position = int(pos_match.group(1))
            else:
                param_position = position if is_positional else None

            # Find detailed information for this parameter
            # Find content before next ### or ##
            param_end = param_match.end()
            next_section = re.search(r"\n###? ", params_text[param_end:])
            if next_section:
                param_detail = params_text[param_end : param_end + next_section.start()]
            else:
                param_detail = params_text[param_end:]

            # Extract type
            type_match = re.search(r"\*\*Type\*\*:\s*`([^`]+)`", param_detail)
            param_type = type_match.group(1) if type_match else "str"

            # Handle list of strings and other types
            if "list" in param_type.lower():
                param_type = "list"
            elif "int" in param_type.lower():
                param_type = "int"
            elif "bool" in param_type.lower():
                param_type = "bool"
            else:
                param_type = "str"

            # Extract description (first non-empty line)
            desc_lines = [
                line.strip()
                for line in param_detail.split("\n")
                if line.strip() and not line.strip().startswith("**")
            ]
            param_desc = desc_lines[0] if desc_lines else f"{param_name} parameter"

            # Extract default value
            default_match = re.search(
                r"default[:\s]+(\d+)", param_detail, re.IGNORECASE
            )
            param_default = int(default_match.group(1)) if default_match else None

            param_def = {
                "name": param_name,
                "type": param_type,
                "description": param_desc,
                "required": is_required,
                "positional": is_positional,
            }

            if is_positional and param_position is not None:
                param_def["position"] = param_position

            if param_default is not None:
                param_def["default"] = param_default

            config["parameters"].append(param_def)
            position += 1

    return config


def _load_bash_tools_from_skills(
    start_dir: str | None = None,
) -> List[BashToolMetadata]:
    """
    Load metadata for all bash tools from skill directories.

    New structure: Each tool is an independent skill directory containing:
    - SKILL.md: Contains YAML frontmatter (name, description) and markdown documentation
    - scripts/: Contains actual bash scripts

    Configuration information is parsed from SKILL.md:
    - YAML frontmatter: should_run_in_sandbox (execution location)
    - Parameters: From ## Parameters section
    - Timeout: From ## Timeout section
    - Returns JSON: From ## Return Value section

    Returns:
        List of BashToolMetadata
    """
    import re

    if not BASH_TOOLS_DIR.exists():
        logger.warning(f"Bash tools directory not found: {BASH_TOOLS_DIR}")
        return []

    base_dir = BASH_TOOLS_DIR
    if start_dir:
        # Allow callers to list tools under a specific subdirectory, e.g.
        # "fuzz" or "static_analysis/get-caller".
        base_dir = (BASH_TOOLS_DIR / start_dir).resolve()
        if not base_dir.exists() or not base_dir.is_dir():
            logger.warning(
                "Start directory not found for bash tools discovery: %s", base_dir
            )
            return []

    tools = []

    def _is_executable_skill_dir(skill_dir: Path) -> bool:
        """Returns True if directory looks like an executable skill (has scripts)."""
        if not (skill_dir / "SKILL.md").exists():
            return False
        scripts_dir = skill_dir / "scripts"
        if not scripts_dir.exists():
            return False
        script_files = list(scripts_dir.glob("*.sh")) + list(scripts_dir.glob("*.py"))
        return bool(script_files)

    # Collect candidate executable skill directories under base_dir.
    # - If base_dir itself is a tool dir, include it.
    # - Always scan up to 2 levels to support layouts like:
    #   - root/tool/SKILL.md
    #   - root/group/tool/SKILL.md  (where group may also have SKILL.md)
    skill_dirs_to_process: list[Path] = []

    if _is_executable_skill_dir(base_dir):
        skill_dirs_to_process.append(base_dir)

    for item in base_dir.iterdir():
        if not item.is_dir():
            continue

        if _is_executable_skill_dir(item):
            skill_dirs_to_process.append(item)

        for subitem in item.iterdir():
            if subitem.is_dir() and _is_executable_skill_dir(subitem):
                skill_dirs_to_process.append(subitem)

        # Process all found skill directories
    for skill_dir in skill_dirs_to_process:
        skill_md_path = skill_dir / "SKILL.md"

        # Read SKILL.md
        with open(skill_md_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Parse YAML frontmatter
        frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not frontmatter_match:
            logger.warning(f"No YAML frontmatter found in {skill_md_path}")
            continue

        frontmatter_text = frontmatter_match.group(1)

        # Simple YAML parsing (extract name, description, returns_json, and sandbox)
        name_match = re.search(r"^name:\s*(.+)$", frontmatter_text, re.MULTILINE)
        desc_match = re.search(r"^description:\s*(.+)$", frontmatter_text, re.MULTILINE)
        returns_json_match = re.search(
            r"^returns_json:\s*(.+)$", frontmatter_text, re.MULTILINE
        )
        sandbox_frontmatter_match = re.search(
            r"^should_run_in_sandbox:\s*(.+)$", frontmatter_text, re.MULTILINE
        )

        if not name_match or not desc_match:
            logger.warning(f"Missing name or description in {skill_md_path}")
            continue

        tool_name = name_match.group(1).strip()
        description = desc_match.group(1).strip()

        # Check if returns_json is explicitly set in frontmatter
        returns_json_from_frontmatter = False
        if returns_json_match:
            value = returns_json_match.group(1).strip().lower()
            returns_json_from_frontmatter = value in ("true", "1", "yes")

        # Find scripts in scripts directory (guaranteed by _is_executable_skill_dir)
        scripts_dir = skill_dir / "scripts"
        script_files = list(scripts_dir.glob("*.sh")) + list(scripts_dir.glob("*.py"))
        script_file = script_files[0]  # Use first found script file

        # Build script path in container (relative to CONTAINER_BASH_TOOLS_DIR).
        #
        # We use the path relative to BASH_TOOLS_DIR so start_dir does not affect
        # the resulting container script path.
        rel_skill_dir = skill_dir.relative_to(BASH_TOOLS_DIR)
        script_path = f"{rel_skill_dir}/scripts/{script_file.name}"

        # Parse configuration from SKILL.md content (parameters/timeout/returns_json).
        config = _parse_skill_md_config(content)

        # Derive execution sandbox type from YAML frontmatter (required).
        if not sandbox_frontmatter_match:
            raise ValueError(
                f"Missing required YAML field 'should_run_in_sandbox' in {skill_md_path}"
            )

        sandbox_value = sandbox_frontmatter_match.group(1).strip()
        # Strip simple surrounding quotes.
        if (sandbox_value.startswith('"') and sandbox_value.endswith('"')) or (
            sandbox_value.startswith("'") and sandbox_value.endswith("'")
        ):
            sandbox_value = sandbox_value[1:-1].strip()
        if not sandbox_value:
            raise ValueError(
                f"Empty YAML field 'should_run_in_sandbox' in {skill_md_path}"
            )

        config["sandbox_types"] = [sandbox_value.lower()]

        # Prefer explicit returns_json from frontmatter, fallback to parsed config
        returns_json = (
            returns_json_from_frontmatter
            if returns_json_match
            else config["returns_json"]
        )

        metadata = BashToolMetadata(
            name=tool_name,
            script_path=script_path,
            description=description,
            parameters=config["parameters"],
            sandbox_types=config["sandbox_types"],
            timeout=config["timeout"],
            returns_json=returns_json,
        )
        tools.append(metadata)

        logger.info(
            f"Loaded skill '{tool_name}' from {skill_md_path}: "
            f"sandbox={config['sandbox_types']}, timeout={config['timeout']}s, "
            f"params={len(config['parameters'])}, returns_json={returns_json}"
        )

    return tools


@safe_tool_execution
def list_available_scripts(
    start_dir: Optional[str] = None, *, tool_context: ToolContext
) -> str:
    """List all available bash scripts and their usage.

    Use this tool to discover what bash scripts are available in the sandbox
    and how to use them. It returns a formatted list of scripts with their
    descriptions and usage examples.

    Args:
        tool_context: Tool context from the agent
        start_dir: Optional subdirectory under bash_tools to start discovery from,
            e.g. "fuzz" or "static_analysis". If omitted, scans all bash_tools.

    Returns:
        str: Formatted list of available scripts and usage instructions
    """
    tools_metadata = _load_bash_tools_from_skills(start_dir=start_dir)

    if not tools_metadata:
        return "No bash tools found in skills directories."

    output = ["Available Bash Scripts:", "=" * 30]

    for meta in tools_metadata:
        output.append(f"\nName: {meta.name}")
        output.append(f"Description: {meta.description}")
        output.append(f"should_run_in_sandbox: {meta.sandbox_types[0]}")

        # Generate usage string
        usage_parts = [meta.name]

        # Sort parameters: positional first, then named
        params = meta.parameters
        positional = sorted(
            [p for p in params if p.get("positional", False)],
            key=lambda p: p.get("position", 0),
        )
        named = [p for p in params if not p.get("positional", False)]

        for p in positional:
            name = p["name"].upper()
            if not p.get("required", True):
                usage_parts.append(f"[{name}]")
            else:
                usage_parts.append(f"<{name}>")

        for p in named:
            name = p["name"]
            p_type = p.get("type", "str")
            if p_type == "bool":
                usage_parts.append(f"[--{name}]")
            else:
                usage_parts.append(f"[--{name} <value>]")

        output.append(f"Usage: {' '.join(usage_parts)}")

        # Add parameter details
        if params:
            output.append("Parameters:")
            for p in positional:
                req = "Required" if p.get("required", True) else "Optional"
                output.append(
                    f"  - {p['name']} (Positional): {p.get('description', '')} [{req}]"
                )
            for p in named:
                req = "Required" if p.get("required", True) else "Optional"
                output.append(
                    f"  - --{p['name']} (Named): {p.get('description', '')} [{req}]"
                )

    return "\n".join(output)


@safe_tool_execution
def run_terminal_command(
    command: str,
    background: bool = False,
    timeout: int = 60,
    sandbox_name: str = "main",
    *,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Execute a command in the sandbox terminal.

    This tool acts like a terminal. You can run any bash command, including
    the scripts listed by `list_available_scripts`. It supports pipes (|),
    redirection (>), and chaining (&&).

    If the command starts with a known script name (e.g., 'run_fuzzing_campaign'),
    it will automatically be executed in the correct sandbox environment (e.g., 'fuzz').
    Otherwise, it runs in the specified sandbox (default: 'main').

    The command you pass in is executed inside the sandbox container as a
    non-interactive process (not a persistent shell session). For background
    execution, the command is written to a temporary script file and then run by
    `bash`, which avoids most wrapper quoting/escaping pitfalls and supports
    multi-line commands. Shell operators like `&&`, `|`, and `>` work as usual.

    Args:
        command: The full command line to execute (e.g., "run_fuzzing_campaign target 30 | grep found")
        background: Whether to run the command in the background (default: False)
        timeout: Timeout in seconds for foreground commands (default: 60)
        sandbox_name: The name of the sandbox to run the command in (default: "main").
                      Ignored if the command is a known script with a forced sandbox type.
        tool_context: The tool context from the agent execution

    Returns:
        dict: Execution result containing 'output', 'exit_code', 'task_id' (if background)
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
    session_id = get_aigise_session_id_from_context(tool_context)
    session = get_aigise_session(session_id)
    if not hasattr(session, "bash_tasks"):
        session.bash_tasks = BashTaskManager()
    task_manager = session.bash_tasks

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
        sandbox, final_command, sandbox_name=target_sandbox
    )
    if not task_id:
        return {"success": False, "error": msg}

    # 2. If background requested, return immediately
    if background:
        return {
            "success": True,
            "task_id": task_id,
            "message": f"Command started in background. Use list_background_tasks to monitor.",
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
        parsed_output = output
        try:
            parsed_output = json.loads(output.strip())
        except Exception as e:
            logger.warning(f"Failed to parse JSON output: {output}")

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
    session_id = get_aigise_session_id_from_context(tool_context)
    session = get_aigise_session(session_id)

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

    Use this tool to get the results from a background task after it has completed.
    You should first call list_background_tasks to find the task_id.

    After successfully consuming the output, this function will:
    1. Delete the temporary files (log, exit code, PID files) from the sandbox
    2. Remove the task from the background task management to free up resources

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
    session_id = get_aigise_session_id_from_context(tool_context)
    session = get_aigise_session(session_id)

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
    cleanup_success = task_manager.cleanup_task(sandbox, task_id)
    result["cleaned_up"] = cleanup_success

    return result
