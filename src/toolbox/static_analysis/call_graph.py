from src.utils.docker_utils import *
from neomodel import db

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
        f"Direct Caller: {row['caller_name']} at {row['path']}:{row['start']}-{row['end']}"
        for row in results if row['caller_name']
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
        f"Maybe Indirect Caller: {row['caller_name']} at {row['path']}:{row['start']}-{row['end']}"
        for row in results if not row['caller_name']
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
        f"Direct Callee: {row['callee_name']} at {row['path']}:{row['start']}-{row['end']}"
        for row in results if row['callee_name']
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
        f"Maybe Indirect Callee: {row['callee_name']} at {row['path']}:{row['start']}-{row['end']}"
        for row in results if not row['callee_name']
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
        f"Direct Callee: {row['callee_name']} at {row['path']}:{row['start']}-{row['end']}"
        for row in results if row['callee_name']
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
        f"Maybe Indirect Caller: {row['caller_name']} at {row['path']}:{row['start']}-{row['end']}"
        for row in results if not row['caller_name']
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
        f"Direct Callee: {row['callee_name']} at {row['path']}:{row['start']}-{row['end']}"
        for row in results if row['callee_name']
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
        f"Maybe Indirect Callee: {row['callee_name']} at {row['path']}:{row['start']}-{row['end']}"
        for row in results if not row['callee_name']
    )
    if not direct_callee and not maybe_indirect_callee:
        return f"No callees found for function '{function_name}' in file '{filepath}'."
    return f"{direct_callee}\n{maybe_indirect_callee}"
    

def get_shortest_paths_in_callgraph_to_function(end_function_name: str, end_function_filepath: str) -> str:
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
    OPTIONAL MATCH p = allShortestPaths(
        (start)-[:DIRECT_CALLS|MAYBE_INDIRECT_CALLS*..10]->(end)
    )
    WHERE p IS NOT NULL
    RETURN start.name AS start_name, [n IN nodes(p) | n.name] AS path
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
    for start_name, path_list in results:
        lines.append(f"From {start_name}: " + " -> ".join(path_list))

    return "\n".join(lines)







