from secagentx.utils.swerex_utils import *
from neomodel import db

def grep_tool_swerex(expression: str) -> dict:
    """
    Search the codebase using SWE-ReX for a given regex pattern.
    This is a SWE-ReX based replacement for the original grep_tool.
    
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
    import os

    container_id = os.getenv("CONTAINER_ID", "default")
    grep_command = " ".join([
        'grep',
        "-rniE",
        expression,  
        "--",
        "/src"  
    ])
    output = ""
    try:
        # Use SWE-ReX instead of run_command_in_container
        output, exit_code = run_command_in_container_swerex(container_id, grep_command)
    except Exception as e:
        return {
            "result": [],
            "error": f"Failed to run grep command: {e}"
        }

    # Split into lines and check count
    lines = output.strip().splitlines()
    if len(lines) > 100 or len(output) > 5000:
        return {
            "result": [],
            "error": "Pattern too broad; please provide a more specific pattern."
        }

    dict_result = {"result": []}
    
    for line in lines:
        if line.strip():
            dict_result["result"].append({
                "full_line": line.strip()
            })

    return dict_result

def list_functions_in_file(filepath: str) -> dict:
    """
    Tool to list all functions in a given file.
    Args:
        filepath (str): The path to the file to search for functions.
    Returns:
        dict: A dictionary with key "result" pointing to a list of function information.
    """
    query = """
    MATCH (f:Function)
    WHERE f.path = $filepath
    RETURN 
        f.name AS function_name,
        f.start AS start,
        f.end AS end
    """
    params = {"filepath": filepath}
    results, _ = db.cypher_query(query, params)

    dict_result = {"result": []}

    for res in results:
        function_name = res[0]
        start = res[1]
        end = res[2]
        if not function_name:
            continue
        dict_result["result"].append({
            "function_name": function_name,
            "filepath": filepath,
            "start_line": start,
            "end_line": end
        })

    return dict_result

def get_line_around_linenum_in_file_swerex(filepath: str, linenum: int, context: int) -> dict:
    """
    Tool to get a specific line and surrounding lines from a file using SWE-ReX.
    Args:
        filepath (str): The path to the file.
        linenum (int): The line number to retrieve.
        context (int): The number of lines of context to include before and after the specified line.
    Returns:
        dict: A dictionary with key "result" pointing to a list of line information.
    """
    import os
    
    try:
        container_id = os.getenv("CONTAINER_ID", "default")
        
        # Use SWE-ReX to read file content
        # First, let's try to read the file using SWE-ReX's file reading capabilities
        # For now, we'll use a simple approach with head/tail/sed
        start_line = max(1, linenum - context)
        end_line = linenum + context
        
        # Create a command to extract the specific lines
        sed_command = f"sed -n '{start_line},{end_line}p' {filepath}"
        
        output, exit_code = run_command_in_container_swerex(container_id, sed_command)
        
        if exit_code != 0:
            return {
                "result": [],
                "error": f"Failed to read file '{filepath}': exit code {exit_code}"
            }
        
        lines = output.strip().splitlines()
        dict_result = {"result": []}
        
        for i, line_content in enumerate(lines):
            line_number = start_line + i
            dict_result["result"].append({
                "filepath": filepath,
                "line_number": line_number,
                "content": line_content
            })
        
        return dict_result
    except Exception as e:
        return {
            "result": [],
            "error": f"Failed to read file '{filepath}': {e}"
        }

# Example of how to use SWE-ReX with interactive sessions
async def interactive_grep_example():
    """
    Example showing how to use SWE-ReX with interactive sessions for more complex operations.
    """
    runner = get_swerex_runner()
    
    try:
        # Start an interactive bash session
        await runner._ensure_runtime()
        await runner.runtime.create_session(CreateBashSessionRequest())
        
        # Run multiple commands in the same session (environment persists)
        output1, exit1 = await runner.run_command_in_session("cd /src")
        output2, exit2 = await runner.run_command_in_session("export GREP_OPTIONS='--color=never'")
        output3, exit3 = await runner.run_command_in_session("grep -rniE 'function.*\\(' . --include='*.py'")
        
        print(f"Found {len(output3.splitlines())} function definitions")
        
    finally:
        await runner.close()

# Example of how to use SWE-ReX for file operations
def file_operations_example():
    """
    Example showing how to use SWE-ReX for file operations.
    """
    runner = get_swerex_runner()
    
    try:
        # List files in a directory
        output, exit_code = runner.run_command_sync("find /src -name '*.py' -type f | head -10")
        print(f"Found Python files: {output}")
        
        # Check file permissions
        output, exit_code = runner.run_command_sync("ls -la /src")
        print(f"Directory listing: {output}")
        
        # Search for specific patterns
        output, exit_code = runner.run_command_sync("grep -r 'import' /src --include='*.py' | wc -l")
        print(f"Total import statements: {output}")
        
    finally:
        runner.close_sync() 