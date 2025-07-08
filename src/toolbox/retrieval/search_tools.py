from src.utils.docker_utils import *
from neomodel import db

def grep_tool(expression: str) -> str:
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
        query (str): A regex pattern to search for.

    Returns:
        str: The grep output (<=100 lines), or an error message if the match set
             is too large.
    """
    import os

    container_id = os.getenv("CONTAINER_ID")
    grep_command = " ".join([
        'grep',
        "-rniE",
        expression,  
        "--",
        "/src"  
    ])
    output = ""
    try:
        output, exit_code = run_command_in_container(container_id, grep_command)
    except Exception as e:
        return f"[ERROR] Failed to run grep command: {e}"

    # Split into lines and check count
    lines = output.strip().splitlines()
    if len(lines) > 100 or len(output)> 5000:
        return "Pattern too broad; please provide a more specific pattern."

    return output


def search_function(function_name: str) -> str:
    """
    Tool to search for a function in the codebase. Input is a function name, output is a string containing the implementation of the function.
    Args:
        function_name (str): The name of the function to search for.
    Returns:
        str: A string containing the implementation for the function.
    """
    query = """
    MATCH (f:Function { name: $function_name })
    RETURN 
        f.path AS path,
        f.start AS start,
        f.end   AS end
    """
    params = {"function_name": function_name}
    results, _ = db.cypher_query(query, params)
    if not results:
        return f"No function named '{function_name}' found in the codebase."
    function_code = ""

    for res in results:
        path = res[0]
        start = res[1]
        end = res[2]
        if not path or not start or not end:
            continue
        function_code += f"Function '{function_name}' found in {path} from line {start} to {end}:\n"
        try:
            # Read the file content from the container
            file_content = read_file_from_container(os.getenv("CONTAINER_ID"), path)
            # Extract the function code using the start and end lines
            lines = file_content.splitlines()
            function_lines = lines[start-1:end]  # Adjust for 0-based index
            function_code += "\n".join(function_lines) + "\n\n"
        except Exception as e:
            continue

    if not function_code:
        return f"Function '{function_name}' has no code associated with it."
    
    return function_code

def list_functions_in_file(filepath: str) -> str:
    """
    Tool to list all functions in a given file.
    Args:
        filepath (str): The path to the file to search for functions.
    Returns:
        str: A string containing the names of all functions in the file.
    """
    query = """
    MATCH (f:Function)
    WHERE f.path = $filepath
    RETURN 
        f.name AS function_name
        f.start AS start,
        f.end AS end
    """
    params = {"filepath": filepath}
    results, _ = db.cypher_query(query, params)

    if not results:
        return f"No functions found in file '{filepath}'."
    
    function_list = []
    for res in results:
        function_name = res[0]
        start = res[1]
        end = res[2]
        if not function_name:
            continue
        if not start or not end:
            function_list.append(f"{function_name} (location unknown)")
        else:
            function_list.append(f"{function_name} (lines {start}-{end})")
    return f"Functions in '{filepath}':\n" + "\n".join(function_list)

def get_line_around_linenum_in_file(filepath: str, linenum: int) -> str:
    """
    Tool to get a specific line and surrounding lines (10 lines) from a file.
    Args:
        filepath (str): The path to the file.
        linenum (int): The line number to retrieve.
    Returns:
        str: A string containing the specified line and its context.
    """
    try:
        file_content = read_file_from_container(os.getenv("CONTAINER_ID"), filepath)
        lines = file_content.splitlines()
        start = max(0, linenum - context - 1)  # Adjust for 0-based index
        end = min(len(lines), linenum + context)  # Adjust for 0-based index
        return "\n".join(lines[start:end])
    except Exception as e:
        return f"[ERROR] Failed to read file '{filepath}': {e}"
