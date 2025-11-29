import logging
import shlex

from google.adk.tools.tool_context import ToolContext

from aigise.toolbox.decorators import requires_sandbox, safe_tool_execution
from aigise.utils.agent_utils import (
    get_neo4j_client_from_context,
    get_sandbox_from_context,
)

logger = logging.getLogger(__name__)

# Neo4j database connection will be set up per function call using session-based approach


@safe_tool_execution
@requires_sandbox("main")
def grep_tool(expression: str, *, tool_context: ToolContext) -> dict:
    """
    Search the codebase inside the running container for a given regex pattern.
    The pattern is passed to grep with flags '-rnE' for recursive, line-numbered,
    extended-regex searches. The expression is used to form the grep command as
    grep_command = [
        "grep",
        "-rniE",
        expression,
        "--",
        "/src"
    ]

    Args:
        expression (str): A regex pattern to search for.

    Returns:
        dict: A dictionary with key "result" pointing to a list of grep matches.
    """
    sandbox = get_sandbox_from_context(tool_context, "main")

    # Escape the expression for shell safety and add limits to prevent broken pipe
    escaped_expression = shlex.quote(expression)

    # Use head to limit output and prevent broken pipe issues
    # The || true ensures the command always exits with code 0 even if grep finds nothing
    grep_command = (
        f"grep -rniE {escaped_expression} -- /src 2>/dev/null | head -150 || true"
    )

    output = ""
    try:
        output, exit_code = sandbox.run_command_in_container(grep_command)
    except Exception as e:
        return {"result": [], "error": f"Failed to run grep command: {e}"}

    # Split into lines and check count
    lines = [line for line in output.strip().splitlines() if line.strip()]
    # if len(lines) > 100 or len(output) > 5000:
    #     return {
    #         "result": [],
    #         "error": "Pattern too broad; please provide a more specific pattern.",
    #     }

    dict_result = {"result": []}

    for line in lines:
        dict_result["result"].append({"full_line": line.strip()})

    return dict_result


@safe_tool_execution
@requires_sandbox("neo4j", "codeql", "joern")
async def list_functions_in_file(filepath: str, *, tool_context: ToolContext) -> dict:
    """
    Tool to list all functions in a given file.
    Args:
        filepath (str): The path to the file to search for functions.
    Returns:
        dict: A dictionary with key "result" pointing to a list of function information.
    """
    try:
        # Use analysis client for static analysis queries
        client = await get_neo4j_client_from_context(tool_context, "analysis")
        query = """
        MATCH (f:Function)
        WHERE f.path = $filepath
        RETURN
            f.name AS function_name,
            f.start AS start,
            f.end AS end
        """
        params = {"filepath": filepath}
        results = await client.run_query(query, params)

        dict_result = {"result": []}

        for res in results:
            function_name = res[0]
            start = res[1]
            end = res[2]
            if not function_name:
                continue
            dict_result["result"].append(
                {
                    "function_name": function_name,
                    "filepath": filepath,
                    "start_line": start,
                    "end_line": end,
                }
            )

        return dict_result
    except Exception as e:
        return {
            "result": [],
            "error": f"Failed to query database for functions in '{filepath}': {str(e)}",
        }


@safe_tool_execution
@requires_sandbox("main")
def get_line_around_linenum_in_file(
    filepath: str, linenum: int, context: int, *, tool_context: ToolContext
) -> dict:
    """
    Tool to get a specific line and surrounding lines from a file.
    Args:
        filepath (str): The path to the file.
        linenum (int): The line number to retrieve.
        context (int): The number of lines of context to include before and after the specified line, DO NOT BE more than 50 lines.
    Returns:
        dict: A dictionary with key "result" pointing to a list of line information.
    """
    try:
        sandbox = get_sandbox_from_context(tool_context, "main")

        file_content = sandbox.extract_file_from_container(filepath)
        lines = file_content.splitlines()
        start = max(0, linenum - context - 1)  # Adjust for 0-based index
        end = min(len(lines), linenum + context)  # Adjust for 0-based index

        result = f"# Extracted lines from {filepath} (lines {start + 1} to {end})\n"

        for i in range(start, end):
            line_number = i + 1  # Convert to 1-based line number
            line_content = lines[i]
            result += f"{line_number:4d}|{line_content}\n"

        return result
    except Exception as e:
        return {"error": f"Failed to read file '{filepath}': {e}"}


@safe_tool_execution
@requires_sandbox("main")
def search_symbol_definition(symbol_name: str, *, tool_context: ToolContext) -> dict:
    """
    Search the codebase inside the running container for the definition of a given symbol.
    If the symbol is a method in a class, do not include the class name in the symbol name.
    Do not include any punctuation such as parentheses in the symbol name.
    Args:
        symbol_name (str): The name of the symbol to search for.
    Returns:
        dict: A dictionary with key "result" pointing to a list of symbol information.
    """
    sandbox = get_sandbox_from_context(tool_context, "main")
    from aigise import get_aigise_session

    aigise_session = get_aigise_session(sandbox.aigise_session_id)
    src_dir_path = aigise_session.config.src_dir_in_sandbox
    # Generate tags file if not exists or regenerate
    output, exit_code = sandbox.run_command_in_container(
        f"ctags --excmd=number --exclude=Makefile -f /shared/.tags -R {src_dir_path}"
    )
    if exit_code != 0:
        output, exit_code = sandbox.run_command_in_container(
            "apt update && apt install ctags"
        )
        if exit_code != 0:
            return {
                "result": [],
                "error": f"Failed to install ctags: {output}, do not call this tool again.",
            }
        output, exit_code = sandbox.run_command_in_container(
            f"ctags --excmd=number --exclude=Makefile -f /shared/.tags -R {src_dir_path}"
        )
        if exit_code != 0:
            return {
                "result": [],
                "error": f"Failed to run ctags command: {output}, do not call this tool again.",
            }

    # First, try exact match (symbol name at start of line followed by tab)
    grep_output, grep_exit = sandbox.run_command_in_container(
        f"grep -i '^{symbol_name}\t' /shared/.tags"
    )

    if grep_exit == 0:
        # Exact match found
        return {"result": grep_output}

    # If no exact match, try fuzzy match (symbol name anywhere in line)
    grep_output, grep_exit = sandbox.run_command_in_container(
        f"grep -i '{symbol_name}' /shared/.tags"
    )

    if grep_exit != 0:
        # No matches found at all
        return {"result": ""}

    # Return fuzzy match results with warning
    return {
        "result": f"Note: No exact match found for '{symbol_name}'. Showing fuzzy matches:\n\n{grep_output}"
    }
