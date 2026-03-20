# -*- coding: utf-8 -*-
import atexit
import logging
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List

from dotenv import load_dotenv
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

logger = logging.getLogger(__name__)

load_dotenv()
PDB_MCP_SSE_PORT = 1112

# Initialize FastMCP server
mcp = FastMCP("mcp-pdb", port=PDB_MCP_SSE_PORT, host="0.0.0.0")


@dataclass
class SessionState:
    """Maintains the complete state of a debugging session"""

    pdb_output_queue: Any = field(default_factory=queue.Queue)
    breakpoints: Dict[str, Any] = field(default_factory=dict)
    pdb_process: Any = None
    pdb_running: bool = False
    current_file: str = None
    current_project_root: str = None
    current_args: str = ""
    current_use_pytest: bool = False
    output_thread: Any = None


session_dict: Dict[ServerSession, SessionState] = {}
# --- Helper Functions ---


def read_pdb_output(process, output_queue):
    """Read output from the pdb process and put it in the queue."""
    try:
        # Use iter() with readline to avoid blocking readline() indefinitely
        # if the process exits unexpectedly or stdout closes.
        for line_bytes in iter(process.stdout.readline, b""):
            output_queue.put(line_bytes.decode("utf-8", errors="replace").rstrip())
    except ValueError:
        # Handle ValueError if stdout is closed prematurely (e.g., process killed)
        logger.warning("PDB output reader: ValueError (stdout likely closed).")
    except Exception as e:
        logger.error(f"PDB output reader: Unexpected error: {e}")
        # Optionally log traceback here if needed
    finally:
        # Ensure stdout is closed if loop finishes normally or breaks
        if process and process.stdout and not process.stdout.closed:
            try:
                process.stdout.close()
            except Exception as e:
                logger.error(f"PDB output reader: Error closing stdout: {e}")
        logger.info("PDB output reader thread finished.")


def get_pdb_output(session_state: SessionState, timeout=0.5):
    """Get accumulated output from the pdb process queue."""
    output = []
    start_time = time.monotonic()
    while True:
        try:
            # Calculate remaining time
            remaining_time = timeout - (time.monotonic() - start_time)
            if remaining_time <= 0:
                break
            pdb_output_queue = session_state.pdb_output_queue
            line = pdb_output_queue.get(timeout=remaining_time)
            output.append(line)
            # Heuristic: If we see the pdb prompt, we likely have the main response
            # Be careful as some commands might produce output containing (Pdb)
            # Let's rely more on the timeout for now, but keep this in mind.
            if line.strip().endswith("(Pdb)"):
                break
        except queue.Empty:
            break  # Timeout reached
    return "\n".join(output)


def send_to_pdb(session_state: SessionState, command, timeout_multiplier=1.0):
    """Send a command to the pdb process and get its response.

    Args:
        command: The PDB command to send
        timeout_multiplier: Multiplier to adjust timeout for complex commands"""

    pdb_process = session_state.pdb_process
    pdb_running = session_state.pdb_running
    pdb_output_queue = session_state.pdb_output_queue
    if pdb_process and pdb_process.poll() is None:
        # Clear queue before sending command to get only relevant output
        while not pdb_output_queue.empty():
            try:
                pdb_output_queue.get_nowait()
            except queue.Empty:
                break

        try:
            # Determine appropriate timeout based on command type
            base_timeout = 1.5
            if command.strip().lower() in ("c", "continue", "r", "run", "until", "unt"):
                timeout = base_timeout * 3 * timeout_multiplier
            else:
                timeout = base_timeout * timeout_multiplier

            pdb_process.stdin.write((command + "\n").encode("utf-8"))
            pdb_process.stdin.flush()
            # Wait a bit for command processing. Adjust if needed.
            output = get_pdb_output(
                session_state, timeout=timeout
            )  # Adjusted timeout for commands

            # Check if process ended right after the command
            if pdb_process.poll() is not None:
                pdb_running = (
                    False  # TODO: also need to update the session_state.pdb_running?
                )
                # Try to get any final output
                final_output = get_pdb_output(session_state, timeout=0.1)
                return f"Command output:\n{output}\n{final_output}\n\n*** The debugging session has ended. ***"

            return output

        except (OSError, BrokenPipeError) as e:
            logger.error(f"Error writing to PDB stdin: {e}")
            pdb_running = False
            # Try to get final output
            final_output = get_pdb_output(session_state, timeout=0.1)
            if pdb_process:
                pdb_process.terminate()  # Ensure process is stopped
                pdb_process.wait(timeout=0.5)
            return f"Error communicating with PDB: {e}\nFinal Output:\n{final_output}\n\n*** The debugging session has likely ended. ***"
        except Exception as e:
            logger.error(f"Unexpected error in send_to_pdb: {e}")
            pdb_running = False
            return f"Unexpected error sending command: {e}"

    elif pdb_running:
        # Process exists but poll() is not None, means it terminated
        pdb_running = False
        final_output = get_pdb_output(session_state, timeout=0.1)
        return f"No active pdb process (it terminated).\nFinal Output:\n{final_output}"
    else:
        return "No active pdb process."


def find_project_root(start_path):
    """Find the project root containing pyproject.toml, .git or other indicators, searching upwards."""
    current_dir = os.path.abspath(start_path)
    # Common project root indicators
    root_indicators = [
        "pyproject.toml",
        ".git",
        "setup.py",
        "requirements.txt",
        "Pipfile",
        "poetry.lock",
    ]

    # Guard against infinite loop if start_path is already root
    while current_dir and current_dir != os.path.dirname(current_dir):
        for indicator in root_indicators:
            if os.path.exists(os.path.join(current_dir, indicator)):
                logger.debug(
                    f"Found project root indicator '{indicator}' at: {current_dir}"
                )
                return current_dir
        current_dir = os.path.dirname(current_dir)

    # Fallback to the starting path's directory if no indicator found
    fallback_dir = os.path.abspath(start_path)
    logger.info(
        f"No common project root indicators found upwards. Falling back to: {fallback_dir}"
    )
    return fallback_dir


def find_venv_details(project_root):
    """Check for virtual environment directories and return python path and bin dir."""
    common_venv_names = [".venv", "venv", "env", ".env", "virtualenv", ".virtualenv"]
    common_venv_locations = [project_root]

    # Also check parent directory as some projects keep venvs one level up
    parent_dir = os.path.dirname(project_root)
    if parent_dir != project_root:  # Avoid infinite loop at filesystem root
        common_venv_locations.append(parent_dir)

    # First check for environment variables pointing to active virtual env
    if "VIRTUAL_ENV" in os.environ:
        venv_path = os.environ["VIRTUAL_ENV"]
        if os.path.isdir(venv_path):
            if sys.platform == "win32":
                python_exe = os.path.join(venv_path, "Scripts", "python.exe")
                bin_dir = os.path.join(venv_path, "Scripts")
            else:
                python_exe = os.path.join(venv_path, "bin", "python")
                bin_dir = os.path.join(venv_path, "bin")

            if os.path.exists(python_exe):
                logger.debug(f"Found active virtual environment: {venv_path}")
                return python_exe, bin_dir

    # Check for conda environment
    if "CONDA_PREFIX" in os.environ:
        conda_path = os.environ["CONDA_PREFIX"]
        if sys.platform == "win32":
            python_exe = os.path.join(conda_path, "python.exe")
            bin_dir = conda_path
        else:
            python_exe = os.path.join(conda_path, "bin", "python")
            bin_dir = os.path.join(conda_path, "bin")

        if os.path.exists(python_exe):
            logger.debug(f"Found conda environment: {conda_path}")
            return python_exe, bin_dir

    for location in common_venv_locations:
        for name in common_venv_names:
            venv_path = os.path.join(location, name)
            if os.path.isdir(venv_path):
                if sys.platform == "win32":
                    python_exe = os.path.join(venv_path, "Scripts", "python.exe")
                    bin_dir = os.path.join(venv_path, "Scripts")
                else:
                    python_exe = os.path.join(venv_path, "bin", "python")
                    bin_dir = os.path.join(venv_path, "bin")

                if os.path.exists(python_exe):
                    logger.debug(f"Found virtual environment: {venv_path}")
                    return python_exe, bin_dir

    # Look for other common Python installations
    if sys.platform == "win32":
        for path in os.environ["PATH"].split(os.pathsep):
            py_exe = os.path.join(path, "python.exe")
            if os.path.exists(py_exe):
                return py_exe, path
    else:
        # On Unix, check if we have a user-installed Python in .local/bin
        local_bin = os.path.expanduser("~/.local/bin")
        if os.path.exists(local_bin):
            for f in os.listdir(local_bin):
                if f.startswith("python") and os.path.isfile(
                    os.path.join(local_bin, f)
                ):
                    py_exe = os.path.join(local_bin, f)
                    return py_exe, local_bin

    logger.info(f"No virtual environment found in: {project_root}")
    return None, None


def sanitize_arguments(args_str):
    """Validate and sanitize command line arguments to prevent injection.

    Raises:
      ValueError: Raised when this operation fails."""
    dangerous_patterns = [";", "&&", "||", "`", "$(", "|", ">", "<"]
    for pattern in dangerous_patterns:
        if pattern in args_str:
            raise ValueError(f"Invalid character in arguments: {pattern}")

    try:
        parsed_args = shlex.split(args_str)
        return parsed_args
    except ValueError as e:
        raise ValueError(f"Error parsing arguments: {e}")


# --- MCP Tools ---


@mcp.tool()
def start_debug(
    file_path: str, use_pytest: bool = False, args: str = "", context: Context = None
) -> str:
    """Start a debugging session on a Python file within its project context.

    Args:
        file_path (str): Path to the Python file or test module to debug.
        use_pytest (bool): If True, run using pytest with --pdb.
        args (str): Additional arguments to pass to the Python script or pytest (space-separated)."""
    session = context.session
    if session not in session_dict:
        session_dict[session] = SessionState()
    session_state = session_dict[session]
    if session_state.pdb_running:
        # Check if the process is *really* still running
        if session_state.pdb_process and session_state.pdb_process.poll() is None:
            return f"Debugging session already running for {session_state.current_file}. Use restart_debug or end_debug first."
        else:
            logger.warning("Detected stale 'pdb_running' state. Resetting.")
            session_state.pdb_running = False  # Reset state if process died

    # --- Validate Input and Find Project ---
    # Try multiple potential locations for the file
    paths_to_check = [
        file_path,  # As provided
        os.path.abspath(file_path),  # Absolute path
        os.path.join(os.getcwd(), file_path),  # Relative to CWD
    ]

    # Add common directories to search in
    for common_dir in ["src", "tests", "lib"]:
        paths_to_check.append(os.path.join(os.getcwd(), common_dir, file_path))

    # Check all possible paths
    abs_file_path = None
    for path in paths_to_check:
        if os.path.exists(path):
            abs_file_path = path
            break

    if not abs_file_path:
        return f"Error: File not found at '{file_path}' (checked multiple locations including CWD, src/, tests/, lib/)"

    file_dir = os.path.dirname(abs_file_path)
    project_root = find_project_root(file_dir)

    # --- Update Global State ---
    session_state.current_project_root = project_root
    session_state.current_file = abs_file_path
    session_state.current_args = args
    session_state.current_use_pytest = use_pytest

    # Store original working directory before changing
    original_working_dir = os.getcwd()
    logger.debug(f"Original working directory: {original_working_dir}")

    # Initialize breakpoints structure for this file if new
    if abs_file_path not in session_state.breakpoints:
        session_state.breakpoints[abs_file_path] = {}

    # Clear the output queue rigorously
    while not session_state.pdb_output_queue.empty():
        try:
            session_state.pdb_output_queue.get_nowait()
        except queue.Empty:
            break

    try:
        # --- Determine Execution Environment ---
        use_uv = False
        uv_path = shutil.which("uv")
        venv_python_path = None
        venv_bin_dir = None

        if uv_path and os.path.exists(os.path.join(project_root, "pyproject.toml")):
            # More reliably check for uv.lock as primary indicator
            if os.path.exists(os.path.join(project_root, "uv.lock")):
                logger.debug("Found uv.lock, assuming uv project.")
                use_uv = True
            else:
                # Optional: Could check pyproject.toml for [tool.uv]
                logger.debug(
                    "Found pyproject.toml and uv executable, tentatively trying uv."
                )
                # We'll let `uv run` determine if it's actually a uv project.
                use_uv = True  # Tentatively true

        if not use_uv:
            # Look for a standard venv if uv isn't detected/used
            venv_python_path, venv_bin_dir = find_venv_details(project_root)

        # --- Prepare Command and Subprocess Environment ---
        cmd = []
        # Start with a clean environment copy, modify selectively
        env = os.environ.copy()

        # Calculate relative path from project root (preferred for tools)
        try:
            rel_file_path = os.path.relpath(abs_file_path, project_root)
            # Handle edge case where file is the project root itself (e.g., debugging a script there)
            if rel_file_path == ".":
                rel_file_path = os.path.basename(abs_file_path)

        except ValueError:
            # Handle cases where file is on a different drive (Windows)
            logger.warning(
                f"File '{abs_file_path}' not relative to project root '{project_root}'. Using absolute path."
            )
            rel_file_path = abs_file_path  # Use absolute path if relative fails

        # Safely parse arguments using sanitize_arguments
        try:
            parsed_args = sanitize_arguments(args)
        except ValueError as e:
            return f"Error in arguments: {e}"

        # Determine command based on environment
        if use_uv:
            logger.info(f"Using uv run in: {project_root}")
            # Clean potentially conflicting env vars for uv run
            env.pop("VIRTUAL_ENV", None)
            env.pop("PYTHONHOME", None)
            base_cmd = ["uv", "run", "--"]
            if use_pytest:
                # -s: show stdout/stderr, --pdbcls: use standard pdb
                base_cmd.extend(["pytest", "--pdb", "-s", "--pdbcls=pdb:Pdb"])
            else:
                base_cmd.extend(["python", "-m", "pdb"])
            cmd = base_cmd + [rel_file_path] + parsed_args
        elif venv_python_path:
            logger.info(f"Using venv Python: {venv_python_path}")
            venv_dir = os.path.dirname(
                os.path.dirname(venv_bin_dir)
            )  # Get actual venv root
            env["VIRTUAL_ENV"] = venv_dir
            env["PATH"] = f"{venv_bin_dir}{os.pathsep}{env.get('PATH', '')}"
            env.pop("PYTHONHOME", None)

            # Critical addition: Set PYTHONPATH to include project root
            env["PYTHONPATH"] = f"{project_root}{os.pathsep}{env.get('PYTHONPATH', '')}"

            # Force unbuffered output for better debugging experience
            env["PYTHONUNBUFFERED"] = "1"

            if use_pytest:
                # Find pytest within the venv
                pytest_exe = os.path.join(
                    venv_bin_dir, "pytest" + (".exe" if sys.platform == "win32" else "")
                )
                if not os.path.exists(pytest_exe):
                    # Try finding via the venv python itself
                    try:
                        result = subprocess.run(
                            [venv_python_path, "-m", "pytest", "--version"],
                            capture_output=True,
                            text=True,
                            check=True,
                            cwd=project_root,
                            env=env,
                        )
                        logger.debug(f"Found pytest via '{venv_python_path} -m pytest'")
                        cmd = [
                            venv_python_path,
                            "-m",
                            "pytest",
                            "--pdb",
                            "-s",
                            "--pdbcls=pdb:Pdb",
                            rel_file_path,
                        ] + parsed_args
                    except (subprocess.CalledProcessError, FileNotFoundError):
                        return f"Error: pytest not found or executable in the virtual environment at {venv_bin_dir}. Cannot run with --pytest."
                else:
                    cmd = [
                        pytest_exe,
                        "--pdb",
                        "-s",
                        "--pdbcls=pdb:Pdb",
                        rel_file_path,
                    ] + parsed_args
            else:
                cmd = [venv_python_path, "-m", "pdb", rel_file_path] + parsed_args
        else:
            logger.warning(
                "No uv or standard venv detected in project root. Using system Python/pytest."
            )
            # Fallback to system python/pytest found in PATH
            python_exe = (
                shutil.which("python") or sys.executable
            )  # Find system python more reliably
            if not python_exe:
                return "Error: Could not find 'python' executable in system PATH."

            if use_pytest:
                pytest_exe = shutil.which("pytest")
                if not pytest_exe:
                    return "Error: pytest command not found in system PATH. Cannot run with --pytest."
                cmd = [
                    pytest_exe,
                    "--pdb",
                    "-s",
                    "--pdbcls=pdb:Pdb",
                    rel_file_path,
                ] + parsed_args
            else:
                cmd = [python_exe, "-m", "pdb", rel_file_path] + parsed_args

        # --- Launch Subprocess ---
        logger.info(f"Executing command: {' '.join(map(shlex.quote, cmd))}")
        logger.debug(f"Working directory: {project_root}")
        logger.debug(f"Using VIRTUAL_ENV: {env.get('VIRTUAL_ENV', 'Not Set')}")

        # Ensure previous thread is not running (important for restarts)
        if session_state.output_thread and session_state.output_thread.is_alive():
            logger.warning("Previous output thread was still alive.")
            # Attempting to join might hang if readline blocks, so we just detach.

        session_state.pdb_process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr to stdout for easier capture
            text=False,  # Read bytes for reliable readline behavior
            cwd=project_root,  # <<< CRITICAL: Run from project root
            env=env,  # Pass the prepared environment
            bufsize=0,  # Use system default buffering (often line-buffered)
        )

        # Start the output reader thread anew
        session_state.output_thread = threading.Thread(
            target=read_pdb_output,
            args=(session_state.pdb_process, session_state.pdb_output_queue),
            daemon=True,  # Allows main program to exit even if thread is running
        )
        session_state.output_thread.start()

        session_state.pdb_running = (
            True  # Set running state *before* waiting for output
        )

        # --- Wait for Initial Output & Verify Start ---
        logger.info("Waiting for PDB to start...")
        initial_output = get_pdb_output(
            session_state, timeout=3.0
        )  # Longer timeout for potentially slow starts/imports

        # Check if process died immediately
        if session_state.pdb_process.poll() is not None:
            exit_code = session_state.pdb_process.poll()
            session_state.pdb_running = False
            # Attempt to get any remaining output directly if thread missed it
            final_out_bytes, _ = session_state.pdb_process.communicate()
            final_out_str = final_out_bytes.decode("utf-8", errors="replace")
            full_output = initial_output + "\n" + final_out_str.strip()
            return (
                f"Error: PDB process exited immediately (Code: {exit_code}). "
                f"Command: {' '.join(map(shlex.quote, cmd))}\n"
                f"Working Dir: {project_root}\n"
                f"Output:\n---\n{full_output}\n---"
            )

        # Check for typical PDB prompt indicators
        # Needs to be somewhat lenient as initial output varies (e.g., pytest header)
        has_pdb_prompt = "-> " in initial_output or "(Pdb)" in initial_output
        has_error = "Error:" in initial_output or "Exception:" in initial_output

        if not has_pdb_prompt:
            # If no prompt but also no obvious error and process is running,
            # it might be okay, just slower startup or waiting.
            if session_state.pdb_process.poll() is None and not has_error:
                warning_msg = (
                    "Warning: PDB started but initial prompt ('-> ' or '(Pdb)') "
                    "not detected in first few seconds. It might be running."
                )
                logger.warning(warning_msg)
                # Proceed but include the warning in the return message
                initial_output = f"{warning_msg}\n\n{initial_output}"
            else:
                # No prompt, process might have died silently or has error message
                session_state.pdb_running = False
                # Try to get more output
                final_output = get_pdb_output(session_state, timeout=0.5)
                full_output = initial_output + "\n" + final_output
                return (
                    f"Error starting PDB. No prompt detected and process may have issues.\n"
                    f"Command: {' '.join(map(shlex.quote, cmd))}\n"
                    f"Working Dir: {project_root}\n"
                    f"Output:\n---\n{full_output}\n---"
                )

        # --- Restore Breakpoints ---
        restored_bps_output = ""
        if (
            session_state.current_file in session_state.breakpoints
            and session_state.breakpoints[session_state.current_file]
        ):
            logger.info(
                f"Restoring {len(session_state.breakpoints[session_state.current_file])} breakpoints for {rel_file_path}..."
            )
            # Use relative path for consistency in breakpoint commands
            try:
                bp_rel_path = os.path.relpath(session_state.current_file, project_root)
                if bp_rel_path == ".":
                    bp_rel_path = os.path.basename(session_state.current_file)
            except ValueError:
                bp_rel_path = session_state.current_file  # Fallback

            restored_bps_output += "\n--- Restoring Breakpoints ---\n"
            # Sort by line number for clarity
            for line_num in sorted(
                session_state.breakpoints[session_state.current_file].keys()
            ):
                bp_command_rel = f"b {bp_rel_path}:{line_num}"
                logger.debug(f"Sending restore cmd: {bp_command_rel}")
                restore_out = send_to_pdb(session_state, bp_command_rel)
                restored_bps_output += (
                    f"Set {bp_rel_path}:{line_num}: {restore_out or '[No Response]'}\n"
                )

                # Extract and update BP number if available
                match = re.search(r"Breakpoint (\d+) at", restore_out)
                if match:
                    bp_data = session_state.breakpoints[session_state.current_file][
                        line_num
                    ]
                    if isinstance(bp_data, dict):
                        bp_data["bp_number"] = match.group(1)
                    else:
                        # Backward compatibility with older format
                        session_state.breakpoints[session_state.current_file][
                            line_num
                        ] = {"command": bp_data, "bp_number": match.group(1)}

            restored_bps_output += "--- Breakpoint Restore Complete ---\n"

        return f"Debugging session started for {rel_file_path} (in {project_root})\n\n{initial_output}\n{restored_bps_output}"

    except FileNotFoundError as e:
        session_state.pdb_running = False
        return f"Error starting debugging session: Command not found ({e.filename}). Is '{cmd[0]}' installed and in the correct PATH (system or venv)?\n{traceback.format_exc()}"
    except Exception as e:
        session_state.pdb_running = False
        return f"Error starting debugging session: {str(e)}\n{traceback.format_exc()}"


@mcp.tool()
def send_pdb_command(command: str, context: Context = None) -> str:
    """Send a command to the running PDB instance.

    Examples:
        n (next line), c (continue), s (step into), r (return from function)
        p variable (print variable), pp variable (pretty print)
        b line_num (set breakpoint in current file), b file:line_num
        cl num (clear breakpoint number), cl file:line_num
        l (list source code), ll (list longer source code)
        a (print arguments of current function)
        q (quit)

    Args:
        command (str): The PDB command string."""
    session = context.session
    if session not in session_dict:
        return "No debugging session found for this context."
    session_state = session_dict[session]
    pdb_process = session_state.pdb_process
    pdb_running = session_state.pdb_running
    if not pdb_running:
        return "No active debugging session. Use start_debug first."

    # Double check process liveness
    if pdb_process is None or pdb_process.poll() is not None:
        session_state.pdb_running = False
        final_output = get_pdb_output(session_state, timeout=0.1)
        return f"The debugging session appears to have ended.\nFinal Output:\n{final_output}"

    try:
        # Determine appropriate timeout based on command complexity
        timeout_multiplier = 1.0
        if command.strip().lower() in ("c", "continue", "r", "run"):
            # These commands might take longer to complete
            timeout_multiplier = 2.0

        response = send_to_pdb(session_state, command, timeout_multiplier)

        # Check if the session ended after this specific command (e.g., 'q' or fatal error)
        if not pdb_running:  # send_to_pdb might set this if process ended
            return (
                f"Command output:\n{response}"  # Response already includes end notice
            )

        # Provide extra context for common navigation commands
        # Only do this if the session is still running
        nav_commands = [
            "n",
            "s",
            "c",
            "r",
            "unt",
            "until",
            "next",
            "step",
            "continue",
            "return",
        ]
        if (
            command.strip().lower() in nav_commands
            and pdb_running
            and pdb_process.poll() is None
        ):
            # Give PDB a tiny bit more time after navigation before asking for location
            # Check again if it's running before sending 'l .'
            if pdb_running and pdb_process.poll() is None:
                logger.debug("Fetching context after navigation...")
                line_context = send_to_pdb(session_state, "l .")
                # Check again after sending 'l .'
                if pdb_running and pdb_process.poll() is None:
                    response += f"\n\n-- Current location --\n{line_context}"
                else:
                    response += "\n\n-- Session ended after navigation --"
                    pdb_running = False  # Ensure state is correct

        return f"Command output:\n{response}"

    except Exception as e:
        # Catch unexpected errors during command sending/processing
        logger.error(f"Error in send_pdb_command: {e}")
        # Check process status again
        if pdb_process and pdb_process.poll() is not None:
            pdb_running = False
            return f"Error sending command: {str(e)}\n\n*** The debugging session has likely ended. ***\n{traceback.format_exc()}"
        else:
            return f"Error sending command: {str(e)}\n{traceback.format_exc()}"


@mcp.tool()
def set_breakpoint(file_path: str, line_number: int, context: Context = None) -> str:
    """Set a breakpoint at a specific line in a file. Uses relative path if possible.

    Args:
        file_path (str): Path to the file (can be relative to project root or absolute).
        line_number (int): Line number for the breakpoint."""
    session = context.session
    if session not in session_dict:
        return "No debugging session found for this context."
    session_state = session_dict[session]

    if not session_state.pdb_running:
        return "No active debugging session. Use start_debug first."
    if not session_state.current_project_root:
        return "Error: Project root not identified. Cannot reliably set breakpoint."

    abs_file_path = os.path.abspath(
        os.path.join(session_state.current_project_root, file_path)
    )  # Resolve relative to root first
    if not os.path.exists(abs_file_path):
        abs_file_path = os.path.abspath(file_path)  # Try absolute directly
        if not os.path.exists(abs_file_path):
            return f"Error: File not found at '{file_path}' (checked relative to project and absolute)."

    # Use relative path for the breakpoint command if possible
    try:
        rel_file_path = os.path.relpath(
            abs_file_path, session_state.current_project_root
        )
        if rel_file_path == ".":
            rel_file_path = os.path.basename(abs_file_path)
    except ValueError:
        rel_file_path = abs_file_path  # Fallback to absolute

    # Track breakpoints using the *absolute* path as the key for internal consistency
    if abs_file_path not in session_state.breakpoints:
        session_state.breakpoints[abs_file_path] = {}

    if line_number in session_state.breakpoints[abs_file_path]:
        # Verify with pdb if it's actually set there
        current_bps = send_to_pdb(session_state, "b")
        if f"{rel_file_path}:{line_number}" in current_bps:
            return f"Breakpoint already exists and is tracked at {abs_file_path}:{line_number}"
        else:
            logger.warning(
                f"Breakpoint tracked locally but not found in PDB output for {rel_file_path}:{line_number}. Will attempt to set."
            )

    command = f"b {rel_file_path}:{line_number}"
    response = send_to_pdb(session_state, command)

    # More robust verification using pattern matching
    bp_markers = ["Breakpoint", "at", str(line_number)]
    if all(marker in response for marker in bp_markers):
        # Extract breakpoint number from response
        match = re.search(r"Breakpoint (\d+) at", response)
        bp_number = match.group(1) if match else None

        # Store both command and breakpoint number
        session_state.breakpoints[abs_file_path][line_number] = {
            "command": command,
            "bp_number": bp_number,
        }
        return f"Breakpoint #{bp_number} set and tracked:\n{response}"
    elif "Error" not in response and "multiple files" not in response.lower():
        # Maybe pdb didn't confirm explicitly but didn't error? (e.g., line doesn't exist yet)
        # We won't track it reliably unless PDB confirms it.
        return f"Breakpoint command sent. PDB response might indicate an issue (e.g., invalid line) or success without standard confirmation:\n{response}\n(Breakpoint NOT reliably tracked. Verify with list_breakpoints)"
    else:
        # PDB reported an error or ambiguity
        return f"Failed to set breakpoint. PDB response:\n{response}"


@mcp.tool()
def clear_breakpoint(file_path: str, line_number: int, context: Context = None) -> str:
    """Clear a breakpoint at a specific line in a file. Uses relative path if possible.

    Args:
        file_path (str): Path to the file where the breakpoint exists.
        line_number (int): Line number of the breakpoint to clear."""
    session = context.session
    if session not in session_dict:
        return "No debugging session found for this context."
    session_state = session_dict[session]

    if not session_state.pdb_running:
        return "No active debugging session. Use start_debug first."
    if not session_state.current_project_root:
        return "Error: Project root not identified. Cannot reliably clear breakpoint."

    abs_file_path = os.path.abspath(
        os.path.join(session_state.current_project_root, file_path)
    )
    if not os.path.exists(abs_file_path):
        abs_file_path = os.path.abspath(file_path)
        if not os.path.exists(abs_file_path):
            # If file doesn't exist, we likely don't have a BP anyway
            if (
                abs_file_path in session_state.breakpoints
                and line_number in session_state.breakpoints[abs_file_path]
            ):
                del session_state.breakpoints[abs_file_path][line_number]
                if not session_state.breakpoints[abs_file_path]:
                    del session_state.breakpoints[abs_file_path]
            return f"Warning: File not found at '{file_path}'. Breakpoint untracked (if it was tracked)."

    try:
        rel_file_path = os.path.relpath(
            abs_file_path, session_state.current_project_root
        )
        if rel_file_path == ".":
            rel_file_path = os.path.basename(abs_file_path)
    except ValueError:
        rel_file_path = abs_file_path

    # Check if we have a breakpoint number stored, which is more reliable for clearing
    bp_number = None
    if (
        abs_file_path in session_state.breakpoints
        and line_number in session_state.breakpoints[abs_file_path]
    ):
        bp_data = session_state.breakpoints[abs_file_path][line_number]
        if isinstance(bp_data, dict) and "bp_number" in bp_data:
            bp_number = bp_data["bp_number"]

    # Use the breakpoint number if available, otherwise use file:line
    if bp_number:
        command = f"cl {bp_number}"
    else:
        command = f"cl {rel_file_path}:{line_number}"

    response = send_to_pdb(session_state, command)

    # Check response for confirmation (e.g., "Deleted breakpoint", "No breakpoint")
    breakpoint_cleared_in_pdb = (
        "Deleted breakpoint" in response
        or "No breakpoint" in response
        or "Error: " not in response
    )

    # Update internal tracking
    if (
        abs_file_path in session_state.breakpoints
        and line_number in session_state.breakpoints[abs_file_path]
    ):
        if breakpoint_cleared_in_pdb:
            del session_state.breakpoints[abs_file_path][line_number]
            if not session_state.breakpoints[
                abs_file_path
            ]:  # Remove file entry if no more bps
                del session_state.breakpoints[abs_file_path]
            status_msg = "Breakpoint untracked."
        else:
            status_msg = "Breakpoint potentially still exists in PDB despite local tracking. Verify with list_breakpoints."
    else:
        status_msg = "Breakpoint was not tracked locally."

    return f"Clear breakpoint result:\n{response}\n({status_msg})"


@mcp.tool()
def list_breakpoints(context: Context = None) -> str:
    """List breakpoints known by PDB and compare with internally tracked breakpoints."""
    session = context.session
    if session not in session_dict:
        return "No debugging session found for this context."
    session_state = session_dict[session]

    if not session_state.pdb_running or not session_state.current_project_root:
        # List only tracked BPs if PDB isn't running or root unknown
        tracked_bps_formatted = []
        for abs_path, lines in session_state.breakpoints.items():
            # Try to show relative if possible, else absolute
            try:
                disp_path = os.path.relpath(
                    abs_path, os.getcwd()
                )  # Relative to current dir might be useful
            except ValueError:
                disp_path = abs_path
            for line_num in sorted(lines.keys()):
                bp_data = lines[line_num]
                if isinstance(bp_data, dict) and "bp_number" in bp_data:
                    tracked_bps_formatted.append(
                        f"{disp_path}:{line_num} (BP #{bp_data['bp_number']})"
                    )
                else:
                    tracked_bps_formatted.append(f"{disp_path}:{line_num}")
        return (
            "No active PDB session or project root unknown.\n\n--- Tracked Breakpoints ---\n"
            + ("\n".join(tracked_bps_formatted) if tracked_bps_formatted else "None")
        )

    pdb_response = send_to_pdb(session_state, "b")

    # Format our tracked breakpoints using relative paths from project root where possible
    tracked_bps_formatted = []
    for abs_path, lines in session_state.breakpoints.items():
        try:
            rel_path = os.path.relpath(abs_path, session_state.current_project_root)
            if rel_path == ".":
                rel_path = os.path.basename(abs_path)
        except ValueError:
            rel_path = abs_path  # Fallback if not relative
        for line_num in sorted(lines.keys()):
            bp_data = lines[line_num]
            if isinstance(bp_data, dict) and "bp_number" in bp_data:
                tracked_bps_formatted.append(
                    f"{rel_path}:{line_num} (BP #{bp_data['bp_number']})"
                )
            else:
                tracked_bps_formatted.append(f"{rel_path}:{line_num}")

    # Add a comparison note
    comparison_note = "\n(Compare PDB list above with tracked list below. Use set/clear to synchronize if needed.)"

    return (
        f"--- PDB Breakpoints ---\n{pdb_response}\n\n"
        f"--- Tracked Breakpoints ---\n"
        + ("\n".join(tracked_bps_formatted) if tracked_bps_formatted else "None")
        + comparison_note
    )


@mcp.tool()
def restart_debug(context: Context = None) -> str:
    """Restart the debugging session with the same file, arguments, and pytest flag."""
    session = context.session
    if session not in session_dict:
        return "No debugging session was previously started (or state lost) to restart."
    session_state = session_dict[session]

    # Store details before ending the current session
    file_to_debug = session_state.current_file
    args_to_use = session_state.current_args
    use_pytest_flag = session_state.current_use_pytest
    logger.info(
        f"Attempting to restart debug for: {file_to_debug} with args='{args_to_use}' pytest={use_pytest_flag}"
    )

    # End the current session forcefully if running
    end_result = "Previous session not running or already ended."
    if session_state.pdb_running:
        logger.info("Ending current session before restart...")
        end_result = end_debug(context=context)  # Use the dedicated end function
        logger.debug(f"Restart: {end_result}")

    # Reset state explicitly (end_debug should handle most, but belt-and-suspenders)
    session_state.pdb_process = None
    session_state.pdb_running = False
    # output_thread should be handled by new start_debug call

    # Clear the output queue again just in case
    while not session_state.pdb_output_queue.empty():
        try:
            session_state.pdb_output_queue.get_nowait()
        except queue.Empty:
            break

    # Start a new session using stored parameters
    logger.info("Calling start_debug for restart...")
    start_result = start_debug(
        file_path=file_to_debug,
        use_pytest=use_pytest_flag,
        args=args_to_use,
        context=context,
    )

    # Note: Breakpoints are now restored within start_debug using the tracked 'breakpoints' dict

    return f"--- Restart Attempt ---\nPrevious session end result: {end_result}\n\nNew session status:\n{start_result}"


@mcp.tool()
def examine_variable(variable_name: str, context: Context = None) -> str:
    """Examine a variable's type, value (print), and attributes (dir) using PDB.

    Args:
        variable_name (str): Name of the variable to examine (e.g., 'my_var', 'self.data')."""
    session = context.session
    if session not in session_dict:
        return "No debugging session found for this context."
    session_state = session_dict[session]
    if not session_state.pdb_running:
        return "No active debugging session. Use start_debug first."

    # Basic print
    p_command = f"p {variable_name}"
    logger.debug(f"Sending command: {p_command}")
    basic_info = send_to_pdb(session_state, p_command)
    if not session_state.pdb_running:
        return f"Session ended after 'p {variable_name}'. Output:\n{basic_info}"

    # Type info
    type_command = f"p type({variable_name})"
    logger.debug(f"Sending command: {type_command}")
    type_info = send_to_pdb(session_state, type_command)
    # Check if session ended, but proceed if possible
    if not session_state.pdb_running and "Session ended" not in basic_info:
        return f"Value:\n{basic_info}\n\nSession ended after 'p type({variable_name})'. Type Output:\n{type_info}"

    # Attributes/methods using dir(), protect with try-except in PDB
    dir_command = (
        f"import inspect; print(dir({variable_name}))"  # More robust than just dir()
    )
    logger.debug("Sending command: (inspect dir)")
    dir_info = send_to_pdb(session_state, dir_command)
    if not session_state.pdb_running and "Session ended" not in type_info:
        return f"Value:\n{basic_info}\n\nType:\n{type_info}\n\nSession ended after 'dir()'. Dir Output:\n{dir_info}"

    # Pretty print (useful for complex objects)
    pp_command = f"pp {variable_name}"
    logger.debug(f"Sending command: {pp_command}")
    pretty_info = send_to_pdb(session_state, pp_command)
    if not session_state.pdb_running and "Session ended" not in dir_info:
        return f"Value:\n{basic_info}\n\nType:\n{type_info}\n\nAttributes/Methods:\n{dir_info}\n\nSession ended after 'pp'. PP Output:\n{pretty_info}"

    return (
        f"--- Variable Examination: {variable_name} ---\n\n"
        f"Value (p):\n{basic_info}\n\n"
        f"Pretty Value (pp):\n{pretty_info}\n\n"
        f"Type (p type()):\n{type_info}\n\n"
        f"Attributes/Methods (dir()):\n{dir_info}\n"
        f"--- End Examination ---"
    )


@mcp.tool()
def get_debug_status(context: Context = None) -> str:
    """Get the current status of the debugging session and tracked state."""
    session = context.session
    if session not in session_dict:
        return "No debugging session found for this context."
    session_state = session_dict[session]
    if not session_state.pdb_running:
        # Check if process exists but isn't running
        if session_state.pdb_process and session_state.pdb_process.poll() is not None:
            return "Debugging session ended. Process terminated."
        return "No active debugging session."

    # Check process liveness again
    if session_state.pdb_process and session_state.pdb_process.poll() is not None:
        session_state.pdb_running = False
        return "Debugging session has ended (process terminated)."

    # Format tracked breakpoints for status
    bp_list = []
    for abs_path, lines in session_state.breakpoints.items():
        try:
            rel_path = os.path.relpath(
                abs_path, session_state.current_project_root or os.getcwd()
            )
            if rel_path == ".":
                rel_path = os.path.basename(abs_path)
        except ValueError:
            rel_path = abs_path
        for line_num in sorted(lines.keys()):
            bp_data = lines[line_num]
            if isinstance(bp_data, dict) and "bp_number" in bp_data:
                bp_list.append(f"{rel_path}:{line_num} (BP #{bp_data['bp_number']})")
            else:
                bp_list.append(f"{rel_path}:{line_num}")

    status = {
        "running": session_state.pdb_running,
        "current_file": session_state.current_file,
        "project_root": session_state.current_project_root,
        "use_pytest": session_state.current_use_pytest,
        "arguments": session_state.current_args,
        "process_id": session_state.pdb_process.pid
        if session_state.pdb_process
        else None,
        "tracked_breakpoints": bp_list,
    }

    # Try to get current location from PDB without advancing
    current_loc_output = "[Could not query PDB location]"
    if (
        session_state.pdb_running
        and session_state.pdb_process
        and session_state.pdb_process.poll() is None
    ):
        current_loc_output = send_to_pdb(
            session_state, "l ."
        )  # Get location without changing state
        if not session_state.pdb_running:  # Check if the query itself ended the session
            status["running"] = False
            current_loc_output += "\n -- Session ended during status check --"

    return (
        "--- Debug Session Status ---\n"
        + f"Running: {status['running']}\n"
        + f"PID: {status['process_id']}\n"
        + f"Project Root: {status['project_root']}\n"
        + f"Debugging File: {status['current_file']}\n"
        + f"Using Pytest: {status['use_pytest']}\n"
        + f"Arguments: '{status['arguments']}'\n"
        + f"Tracked Breakpoints: {status['tracked_breakpoints'] or 'None'}\n\n"
        + f"-- Current PDB Location --\n{current_loc_output}\n"
        + "--- End Status ---"
    )


@mcp.tool()
def end_debug(context: Context = None) -> str:
    """End the current debugging session forcefully."""
    session = context.session
    if session not in session_dict:
        return "No debugging session found for this context."
    session_state = session_dict[session]

    if not session_state.pdb_running and (
        session_state.pdb_process is None
        or session_state.pdb_process.poll() is not None
    ):
        return "No active debugging session to end."

    logger.info("Ending debugging session...")
    result_message = "Debugging session ended."

    if session_state.pdb_process and session_state.pdb_process.poll() is None:
        try:
            # Try sending SIGINT (Ctrl+C) first for cleaner exit
            if sys.platform != "win32":
                try:
                    os.kill(session_state.pdb_process.pid, signal.SIGINT)
                    try:
                        session_state.pdb_process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        pass
                except (OSError, ProcessLookupError) as e:
                    logger.error(f"SIGINT failed: {e}")

            # Next try sending quit command for graceful exit
            if session_state.pdb_process.poll() is None:
                try:
                    logger.info("Attempting graceful exit with 'q'...")
                    session_state.pdb_process.stdin.write(b"q\n")
                    session_state.pdb_process.stdin.flush()
                    # Wait briefly for potential cleanup
                    session_state.pdb_process.wait(timeout=0.5)
                    logger.info("PDB process quit gracefully.")
                except (subprocess.TimeoutExpired, OSError, BrokenPipeError) as e:
                    logger.warning(
                        f"Graceful quit failed or timed out ({e}). Terminating forcefully."
                    )

            # If still running, terminate forcefully
            if session_state.pdb_process.poll() is None:
                try:
                    session_state.pdb_process.terminate()  # Send SIGTERM
                    session_state.pdb_process.wait(timeout=1.0)  # Wait for termination
                    logger.info("PDB process terminated.")
                except subprocess.TimeoutExpired:
                    logger.warning("Terminate timed out. Killing process.")
                    session_state.pdb_process.kill()  # Send SIGKILL
                    session_state.pdb_process.wait(timeout=0.5)  # Wait for kill
                    logger.info("PDB process killed.")
                except Exception as term_err:
                    logger.error(f"Error during terminate/kill: {term_err}")
                    result_message = f"Debugging session ended with errors during termination: {term_err}"
        except Exception as e:
            logger.error(f"Error during end_debug: {e}")
            result_message = f"Debugging session ended with errors: {e}"

    # Clean up state

    # Wait briefly for the output thread to potentially finish reading remaining output
    if session_state.output_thread and session_state.output_thread.is_alive():
        logger.info("Waiting for output thread to finish...")
        session_state.output_thread.join(timeout=0.5)
        if session_state.output_thread.is_alive():
            logger.warning("Output thread did not finish cleanly.")

    session_state.output_thread = None  # Clear thread object reference

    # Clear the queue one last time
    while not session_state.pdb_output_queue.empty():
        try:
            session_state.pdb_output_queue.get_nowait()
        except queue.Empty:
            break

    logger.info("Debugging session ended and state cleared.")
    return result_message


# --- Cleanup on Exit ---


def cleanup(context: Context = None):
    """Ensure the PDB process is terminated when the MCP server exits."""
    logger.info("Running atexit cleanup...")
    session = context.session
    if session not in session_dict:
        return "No debugging session found for this context."
    session_state = session_dict[session]
    pdb_process = session_state.pdb_process
    pdb_running = session_state.pdb_running
    if pdb_running or (pdb_process and pdb_process.poll() is None):
        end_debug(context=context)


atexit.register(cleanup)

# --- Main Execution ---


def main():
    """Initialize and run the FastMCP server."""
    logger.info("--- Starting MCP PDB Tool Server ---")
    logger.info(f"Python Executable: {sys.executable}")
    logger.info(f"Working Directory: {os.getcwd()}")
    # Add any other relevant startup info here
    mcp.run(transport="sse")
    logger.info("--- MCP PDB Tool Server Shutdown ---")


if __name__ == "__main__":
    main()
