import base64
import logging
import shlex
import textwrap
from typing import Any, Dict, Optional

from google.adk.tools.tool_context import ToolContext

from aigise.toolbox.decorators import safe_tool_execution
from aigise.utils.agent_utils import get_sandbox_from_context

logger = logging.getLogger(__name__)


def _run_python_script(sandbox, script: str, description: str) -> str:
    """Helper to run a generated python script safely in the container."""
    encoded_script = base64.b64encode(script.encode("utf-8")).decode("utf-8")
    # We unwrap the script inside the container and execute it
    cmd = f"python3 -c \"import base64; exec(base64.b64decode('{encoded_script}').decode('utf-8'))\""

    logger.info(f"Running file operation ({description}): {cmd}")
    output, exit_code = sandbox.run_command_in_container(cmd)

    if exit_code != 0:
        return f"Error ({exit_code}): {output}"
    return output


@safe_tool_execution
def view_file(
    path: str, start_line: int = 1, end_line: int = -1, *, tool_context: ToolContext
) -> str:
    """
    View the contents of a file, specifying a line range.
    Lines are numbered.

    Args:
        path: Path to the file.
        start_line: Starting line number (1-indexed, default 1).
        end_line: Ending line number (inclusive, default -1 for end of file).
        tool_context: Tool context.

    Returns:
        The content of the file within the range, prefixed with line numbers.
    """
    sandbox = get_sandbox_from_context(tool_context, "main")

    # Check if file exists first
    check_cmd = f"test -f {shlex.quote(path)}"
    _, exit_code = sandbox.run_command_in_container(check_cmd)
    if exit_code != 0:
        return f"Error: File {path} not found or not a regular file."

    # Use nl to number lines, then sed to filter range
    # nl -b a: number all lines
    # sed -n '{start},{end}p'
    range_spec = f"{start_line},$" if end_line == -1 else f"{start_line},{end_line}"

    cmd = f"nl -b a {shlex.quote(path)} | sed -n '{range_spec}p'"
    output, exit_code = sandbox.run_command_in_container(cmd)

    if exit_code != 0:
        return f"Error viewing file: {output}"

    return output


@safe_tool_execution
def edit_file(
    path: str,
    content: str,
    start_line: int,
    end_line: int,
    *,
    tool_context: ToolContext,
) -> str:
    """
    Replace lines [start_line, end_line] (inclusive) in a file with new content.
    To insert without replacing, usage depends on logic, but typically you replace a range.
    To delete, provide empty content.

    Args:
        path: Path to the file.
        content: New content to insert/replace.
        start_line: Start line number (1-indexed).
        end_line: End line number (1-indexed, inclusive).
        tool_context: Tool context.

    Returns:
        Success message or error.
    """
    sandbox = get_sandbox_from_context(tool_context, "main")

    # We use a python script to handle file IO cleanly to avoid shell escaping issues
    # and to handle newlines correctly.

    script = textwrap.dedent(f"""
        import sys
        import base64
        import os

        path = "{path}"

        # Decode content
        content_b64 = "{base64.b64encode(content.encode("utf-8")).decode("utf-8")}"
        new_content = base64.b64decode(content_b64).decode('utf-8')

        start = {start_line}
        end = {end_line}

        if not os.path.exists(path):
            print(f"Error: File {{path}} not found")
            sys.exit(1)

        with open(path, 'r') as f:
            lines = f.readlines()

        # Validate bounds
        # start is 1-indexed
        if start < 1:
             print(f"Error: Start line {{start}} must be >= 1")
             sys.exit(1)

        # Convert to 0-indexed slice
        idx_start = start - 1
        idx_end = end # slice is exclusive, but end_line is inclusive, so end (idx) match

        # If start is beyond end of file, we append?
        # Standard behavior: if start > len, maybe just append?
        # Let's enforce bounds strictly for safety/clarity unless it gets annoying.
        if idx_start > len(lines):
             print(f"Error: Start line {{start}} is beyond EOF ({{len(lines)}} lines)")
             sys.exit(1)

        # Prepare new lines
        # Determine if we need to add a newline to the new content chunks
        # Usually user provides a block. We split it into lines.
        replacement_lines = new_content.splitlines(keepends=True)

        # If the input string didn't have a trailing newline but we are inserting as lines,
        # we might want to ensure consistency.
        # But `splitlines(keepends=True)` keeps \n if present.
        # If user sends "a\nb", we get ["a\n", "b"].
        # If we insert "b" into middle of file, it merges with next line if no \n.
        # Let's trust the user's content exactly.

        lines[idx_start:idx_end] = replacement_lines

        with open(path, 'w') as f:
            f.writelines(lines)

        print(f"Successfully edited {{path}} (Replaced lines {{start}}-{{end}})")
    """)

    return _run_python_script(sandbox, script, "edit_file")


@safe_tool_execution
def search_file(path: str, regex: str, *, tool_context: ToolContext) -> str:
    """
    Search for a regular expression in a file.

    Args:
        path: Path to the file.
        regex: valid python/grep regex pattern.
        tool_context: Tool context.

    Returns:
        Matching lines with line numbers.
    """
    sandbox = get_sandbox_from_context(tool_context, "main")

    # Use grep -nE for extended regex and line numbers
    # Ensure regex is quoted
    cmd = f"grep -nE {shlex.quote(regex)} {shlex.quote(path)}"
    output, exit_code = sandbox.run_command_in_container(cmd)

    if exit_code == 1:
        return "No matches found."
    elif exit_code != 0:
        return f"Error searching file: {output}"

    return output


@safe_tool_execution
def replace_in_file(
    path: str, old_text: str, new_text: str, *, tool_context: ToolContext
) -> str:
    """
    Replace all occurrences of a string with another string in a file.
    Performs exact string replacement (not regex).

    Args:
        path: Path to the file.
        old_text: The exact string to find.
        new_text: The string to replace it with.
        tool_context: Tool context.

    Returns:
        Success message or error.
    """
    sandbox = get_sandbox_from_context(tool_context, "main")

    script = textwrap.dedent(f"""
        import sys
        import base64
        import os

        path = "{path}"

        old_b64 = "{base64.b64encode(old_text.encode("utf-8")).decode("utf-8")}"
        new_b64 = "{base64.b64encode(new_text.encode("utf-8")).decode("utf-8")}"

        old_str = base64.b64decode(old_b64).decode('utf-8')
        new_str = base64.b64decode(new_b64).decode('utf-8')

        if not os.path.exists(path):
            print(f"Error: File {{path}} not found")
            sys.exit(1)

        with open(path, 'r') as f:
            content = f.read()

        if old_str not in content:
            print(f"Warning: String not found in {{path}}. No changes made.")
            # We don't exit 1, just warn?
            sys.exit(0)

        new_content = content.replace(old_str, new_str)

        with open(path, 'w') as f:
            f.write(new_content)

        print(f"Successfully replaced text in {{path}}")
    """)

    return _run_python_script(sandbox, script, "replace_in_file")


@safe_tool_execution
def list_dir(path: str = ".", *, tool_context: ToolContext) -> str:
    """
    List contents of a directory.

    Args:
        path: Directory path (default current dir).
        tool_context: Tool context.

    Returns:
        Directory listing.
    """
    sandbox = get_sandbox_from_context(tool_context, "main")

    # ls -F appends / to dirs, * to executables
    cmd = f"ls -F {shlex.quote(path)}"
    output, exit_code = sandbox.run_command_in_container(cmd)

    if exit_code != 0:
        return f"Error listing directory: {output}"

    return output
