from secagentx.utils.docker_utils import *
from neomodel import db

db.set_connection(f"bolt://{os.getenv('NEO4J_USER')}:{os.getenv('NEO4J_PASSWORD')}@{os.getenv('NEO4J_URI_SUFFIX')}")

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
            file_content = extract_file_from_container(os.getenv("CONTAINER_ID"), path)
            # Extract the function code using the start and end lines
            lines = file_content.splitlines()
            function_lines = lines[start-2:end]  # Adjust for 0-based index
            function_code += "\n".join(function_lines) + "\n\n"
        except Exception as e:
            continue

    if not function_code:
        return f"Function '{function_name}' has no code associated with it."
    
    return function_code

def get_caller_by_funcname(function_name: str) -> str:
    """
    Tool to get the caller of a function in the codebase. Input is a function name, output is a string containing the callers of the function.
    Args:
        function_name (str): The name of the function to search for.
    Returns:
        str: A string containing the callers for the function.
    """
    direct_query = """
    MATCH (f:Function { name: $function_name })<-[:DIRECT_CALLS]-(caller:Function)
    RETURN 
        caller.name AS caller_name,
        caller.path AS path,
        caller.start AS start,
        caller.end AS end
    """
    params = {"function_name": function_name}
    results, _ = db.cypher_query(direct_query, params)

    direct_caller = "\n".join(
        f"Direct Caller: {row[0]} at {row[1]}:{row[2]}-{row[3]}"
        for row in results if row[0]
    )

    indirect_query = """
    MATCH (f:Function { name: $function_name })<-[:MAYBE_INDIRECT_CALLS]-(caller:Function)
    RETURN 
        caller.name AS caller_name,
        caller.path AS path,
        caller.start AS start,
        caller.end AS end
    """

    results, _ = db.cypher_query(indirect_query, params)

    maybe_indirect_caller = "\n".join(
        f"Maybe Indirect Caller: {row[0]} at {row[1]}:{row[2]}-{row[3]}"
        for row in results if row[0]
    )

    if not direct_caller and not maybe_indirect_caller:
        return f"No callers found for function '{function_name}' in the codebase."
    return f"{direct_caller}\n{maybe_indirect_caller}"

def get_callee_by_funcname(function_name: str) -> str:
    """
    Tool to get the callee of a function in the codebase. Input is a function name, output is a string containing the callees of the function.
    Args:
        function_name (str): The name of the function to search for.
    Returns:
        str: A string containing the callees for the function.
    """
    direct_query = """
    MATCH (f:Function { name: $function_name })-[:DIRECT_CALLS]->(callee:Function)
    RETURN 
        callee.name AS callee_name,
        callee.path AS path,
        callee.start AS start,
        callee.end AS end
    """
    params = {"function_name": function_name}
    results, _ = db.cypher_query(direct_query, params)

    direct_callee = "\n".join(
        f"Direct Callee: {row[0]} at {row[1]}:{row[2]}-{row[3]}"
        for row in results if row[0]
    )

    indirect_query = """
    MATCH (f:Function { name: $function_name })-[:MAYBE_INDIRECT_CALLS]->(callee:Function)
    RETURN 
        callee.name AS callee_name,
        callee.path AS path,
        callee.start AS start,
        callee.end AS end
    """

    results, _ = db.cypher_query(indirect_query, params)

    maybe_indirect_callee = "\n".join(
        f"Maybe Indirect Callee: {row[0]} at {row[1]}:{row[2]}-{row[3]}"
        for row in results if row[0]
    )

    if not direct_callee and not maybe_indirect_callee:
        return f"No callees found for function '{function_name}' in the codebase."
    
    return f"{direct_callee}\n{maybe_indirect_callee}"

def get_caller_by_funcname_and_filepath(function_name: str, filepath: str) -> str:
    """
    Tool to get the caller of a function in the codebase by function name and file path.
    Args:
        function_name (str): The name of the function to search for.
        filepath (str): The file path where the function is defined.
    Returns:
        str: A string containing the callers of the function.
    """
    direct_query = """
    MATCH (f:Function { name: $function_name, path: $filepath })<-[:DIRECT_CALLS]-(caller:Function)
    RETURN 
        caller.name AS caller_name,
        caller.path AS path,
        caller.start AS start,
        caller.end AS end
    """
    params = {"function_name": function_name, "filepath": filepath}
    results, _ = db.cypher_query(direct_query, params)

    direct_caller = "\n".join(
        f"Direct Caller: {row[0]} at {row[1]}:{row[2]}-{row[3]}"
        for row in results if row[0]
    )

    indirect_query = """
    MATCH (f:Function { name: $function_name, path: $filepath })<-[:MAYBE_INDIRECT_CALLS]-(caller:Function)
    RETURN 
        caller.name AS caller_name,
        caller.path AS path,
        caller.start AS start,
        caller.end AS end
    """
    params = {"function_name": function_name, "filepath": filepath}
    results, _ = db.cypher_query(indirect_query, params)
    maybe_indirect_caller = "\n".join(
        f"Maybe Indirect Caller: {row[0]} at {row[1]}:{row[2]}-{row[3]}"
        for row in results if row[0]
    )
    if not direct_caller and not maybe_indirect_caller:
        return f"No callers found for function '{function_name}' in file '{filepath}'."
    return f"{direct_caller}\n{maybe_indirect_caller}"


def get_callee_by_funcname_and_filepath(function_name: str, filepath: str) -> str:
    """
    Tool to get the callee of a function in the codebase by function name and file path.
    Args:
        function_name (str): The name of the function to search for.
        filepath (str): The file path where the function is defined.
    Returns:
        str: A string containing the callees of the function.
    """
    direct_query = """
    MATCH (f:Function { name: $function_name, path: $filepath })-[:DIRECT_CALLS]->(callee:Function)
    RETURN 
        callee.name AS callee_name,
        callee.path AS path,
        callee.start AS start,
        callee.end AS end
    """
    params = {"function_name": function_name, "filepath": filepath}
    results, _ = db.cypher_query(direct_query, params)

    direct_callee = "\n".join(
        f"Direct Callee: {row[0]} at {row[1]}:{row[2]}-{row[3]}"
        for row in results if row[0]
    )
    indirect_query = """
    MATCH (f:Function { name: $function_name, path: $filepath })-[:MAYBE_INDIRECT_CALLS]->(callee:Function)
    RETURN 
        callee.name AS callee_name,
        callee.path AS path,
        callee.start AS start,
        callee.end AS end
    """
    params = {"function_name": function_name, "filepath": filepath}
    results, _ = db.cypher_query(indirect_query, params)
    maybe_indirect_callee = "\n".join(
        f"Maybe Indirect Callee: {row[0]} at {row[1]}:{row[2]}-{row[3]}"
        for row in results if row[0]
    )
    if not direct_callee and not maybe_indirect_callee:
        return f"No callees found for function '{function_name}' in file '{filepath}'."
    return f"{direct_callee}\n{maybe_indirect_callee}"
    

def get_shortest_paths_in_callgraph_to_function_in_file(end_function_name: str, end_function_filepath: str) -> str:
    """
    Tool to get the shortest paths from each fuzzing entrypoint (LLVMFuzzerTestOneInput)
    to a specified end function in the codebase.

    Args:
        end_function_name (str): The name of the target end function.
        end_function_filepath (str): The file path where the end function is defined.

    Returns:
        str: For each entrypoint, a line describing the shortest path of function names
             from the entrypoint to the end function. If no paths are found for any
             entrypoint, returns a message indicating no path was found.
    """
    query = """
    MATCH (end:Function {
        name: $end_function_name,
        path: $end_function_filepath
    })
    MATCH (start:Function)
    WHERE start.name CONTAINS "LLVMFuzzerTestOneInput"
    WITH start, end
    MATCH p = allShortestPaths(
        (start)-[:DIRECT_CALLS|MAYBE_INDIRECT_CALLS*..10]->(end)
    )
    WHERE p IS NOT NULL
    RETURN 
      start.name AS start_name,
      [n IN nodes(p) | {
         name: n.name,
         path: n.path,
         start: n.start,
         end: n.end
      }] AS path_nodes
    ORDER BY start_name
    """
    params = {
        "end_function_name": end_function_name,
        "end_function_filepath": end_function_filepath
    }
    results, _ = db.cypher_query(query, params)

    if not results:
        return (
            f"No path found to function '{end_function_name}' "
            f"in file '{end_function_filepath}'."
        )

    lines = []
    for start_name, path_nodes in results:
        nodes_str = []
        for node in path_nodes:
            nodes_str.append(
                f"{node['name']} ({node['path']}:line {node['start']}-line {node['end']})"
            )
        lines.append(f"From {start_name}:\n  " + "\n  ".join(nodes_str))

    return "\n\n".join(lines)





