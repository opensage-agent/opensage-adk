from secagentx.utils.docker_utils import *
from neomodel import db

def grep_tool(expression: str) -> dict:
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

def get_line_around_linenum_in_file(filepath: str, linenum: int, context: int) -> dict:
    """
    Tool to get a specific line and surrounding lines from a file.
    Args:
        filepath (str): The path to the file.
        linenum (int): The line number to retrieve.
        context (int): The number of lines of context to include before and after the specified line.
    Returns:
        dict: A dictionary with key "result" pointing to a list of line information.
    """
    try:
        file_content = extract_file_from_container(os.getenv("CONTAINER_ID"), filepath)
        lines = file_content.splitlines()
        start = max(0, linenum - context - 1)  # Adjust for 0-based index
        end = min(len(lines), linenum + context)  # Adjust for 0-based index
        
        dict_result = {"result": []}
        
        for i in range(start, end):
            line_number = i + 1  # Convert to 1-based line number
            line_content = lines[i] if i < len(lines) else ""
            
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
