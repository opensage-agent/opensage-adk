import os

from google.adk.tools.tool_context import ToolContext
from neomodel import db

from aigise.sandbox.docker_config import DockerConfig
from aigise.sandbox_manager import SandboxManager

db.set_connection(
    f"bolt://{os.getenv('NEO4J_USER')}:{os.getenv('NEO4J_PASSWORD')}@{os.getenv('NEO4J_URI_SUFFIX')}"
)


def search_function(function_name: str, *, tool_context: ToolContext) -> dict:
    """
    Tool to search for a function in the codebase. Input is a function name, output is a dictionary containing the implementation of the function.
    Args:
        function_name (str): The name of the function to search for.
    Returns:
        dict: A dictionary with key "result" pointing to a list of function information.
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

    dict_result = {"result": []}

    if not results:
        return dict_result

    for res in results:
        path = res[0]
        start = res[1]
        end = res[2]
        if not path or not start or not end:
            continue

        try:
            # Get sandbox from SandboxManager
            tool_context.state.get("root_session_id")
            docker_config = DockerConfig(image=os.getenv("IMAGE_NAME"))
            sandbox = SandboxManager.get_sandbox(session_id, docker_config)

            # Read the file content from the container using sandbox
            file_content = sandbox.extract_file_from_container(path)
            # Extract the function code using the start and end lines
            lines = file_content.splitlines()
            function_lines = lines[start - 2 : end]  # Adjust for 0-based index
            function_code = "\n".join(function_lines)

            dict_result["result"].append(
                {
                    "function_name": function_name,
                    "filepath": path,
                    "start_line": start,
                    "end_line": end,
                    "function_code": function_code,
                }
            )
        except Exception as e:
            continue

    return dict_result


def get_caller_by_funcname(function_name: str) -> dict:
    """
    Tool to get the caller of a function in the codebase. Input is a function name, output is a list of dicts containing
    the caller function name, file path, and start/end line numbers.
    Args:
        function_name (str): The name of the function to search for.
    Returns:
        dict: A dictionary with key "result" pointing to a list of caller information.
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

    dict_result = {"result": []}

    for row in results:
        if row[0]:
            dict_result["result"].append(
                {
                    "function_name": row[0],
                    "filepath": row[1],
                    "start_line": row[2],
                    "end_line": row[3],
                    "call_type": "direct",
                }
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

    for row in results:
        if row[0]:
            dict_result["result"].append(
                {
                    "function_name": row[0],
                    "filepath": row[1],
                    "start_line": row[2],
                    "end_line": row[3],
                    "call_type": "maybe_indirect",
                }
            )

    return dict_result


def get_callee_by_funcname(function_name: str) -> dict:
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

    dict_result = {"result": []}

    # Direct callees
    direct_query = """
    MATCH (f:Function { name: $function_name })-[:DIRECT_CALLS]->(callee:Function)
    RETURN
        callee.name AS callee_name,
        callee.path AS path,
        callee.start AS start,
        callee.end AS end
    """
    results, _ = db.cypher_query(direct_query, params)
    for row in results:
        if row[0]:
            dict_result["result"].append(
                {
                    "function_name": row[0],
                    "filepath": row[1],
                    "start_line": row[2],
                    "end_line": row[3],
                    "call_type": "direct",
                }
            )

    # Maybe indirect callees
    indirect_query = """
    MATCH (f:Function { name: $function_name })-[:MAYBE_INDIRECT_CALLS]->(callee:Function)
    RETURN
        callee.name AS callee_name,
        callee.path AS path,
        callee.start AS start,
        callee.end AS end
    """
    results, _ = db.cypher_query(indirect_query, params)
    for row in results:
        if row[0]:
            dict_result["result"].append(
                {
                    "function_name": row[0],
                    "filepath": row[1],
                    "start_line": row[2],
                    "end_line": row[3],
                    "call_type": "maybe_indirect",
                }
            )

    return dict_result


def get_caller_by_funcname_and_filepath(function_name: str, filepath: str) -> dict:
    """
    Tool to get the caller of a function in the codebase by function name and file path.
    Args:
        function_name (str): The name of the function to search for.
        filepath (str): The file path where the function is defined.
    Returns:
        dict: A dictionary with key "result" pointing to a list of caller information.
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

    dict_result = {"result": []}

    for row in results:
        if row[0]:
            dict_result["result"].append(
                {
                    "function_name": row[0],
                    "filepath": row[1],
                    "start_line": row[2],
                    "end_line": row[3],
                    "call_type": "direct",
                }
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

    for row in results:
        if row[0]:
            dict_result["result"].append(
                {
                    "function_name": row[0],
                    "filepath": row[1],
                    "start_line": row[2],
                    "end_line": row[3],
                    "call_type": "maybe_indirect",
                }
            )

    return dict_result


def get_callee_by_funcname_and_filepath(function_name: str, filepath: str) -> dict:
    """
    Tool to get the callee of a function in the codebase by function name and file path.
    Args:
        function_name (str): The name of the function to search for.
        filepath (str): The file path where the function is defined.
    Returns:
        dict: A dictionary with key "result" pointing to a list of callee information.
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

    dict_result = {"result": []}

    for row in results:
        if row[0]:
            dict_result["result"].append(
                {
                    "function_name": row[0],
                    "filepath": row[1],
                    "start_line": row[2],
                    "end_line": row[3],
                    "call_type": "direct",
                }
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

    for row in results:
        if row[0]:
            dict_result["result"].append(
                {
                    "function_name": row[0],
                    "filepath": row[1],
                    "start_line": row[2],
                    "end_line": row[3],
                    "call_type": "maybe_indirect",
                }
            )

    return dict_result


def get_shortest_paths_in_callgraph_to_function_in_file(
    function_name: str, filepath: str
) -> dict:
    """
    Tool to get the shortest paths from each fuzzing entrypoint (LLVMFuzzerTestOneInput)
    to a specified end function in the codebase.

    Args:
        function_name (str): The name of the target end function.
        filepath (str): The file path where the end function is defined.

    Returns:
        dict: A dictionary with key "result" pointing to a list of path information.
    """
    query = """
    MATCH (end:Function {
        name: $function_name,
        path: $filepath
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
    params = {"function_name": function_name, "filepath": filepath}
    results, _ = db.cypher_query(query, params)

    dict_result = {"result": []}

    if not results:
        return dict_result

    for start_name, path_nodes in results:
        path_info = {
            "start_function": start_name,
            "target_function": function_name,
            "target_filepath": filepath,
            "path_nodes": [],
        }

        for node in path_nodes:
            path_info["path_nodes"].append(
                {
                    "function_name": node["name"],
                    "filepath": node["path"],
                    "start_line": node["start"],
                    "end_line": node["end"],
                }
            )

        dict_result["result"].append(path_info)

    return dict_result
