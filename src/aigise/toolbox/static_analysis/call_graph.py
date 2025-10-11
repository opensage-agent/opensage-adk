from google.adk.tools.tool_context import ToolContext

from aigise.toolbox.decorators import requires_sandbox
from aigise.utils.agent_utils import (
    get_neo4j_client_from_context,
    get_sandbox_from_context,
)

# Setup Neo4j connection using config manager with fallback


@requires_sandbox("neo4j", "main", "codeql", "joern")
async def search_function(function_name: str, *, tool_context: ToolContext) -> dict:
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
    # Use analysis client for static analysis queries
    client = await get_neo4j_client_from_context(tool_context, "analysis")

    params = {"function_name": function_name}
    results = await client.run_query(query, params)

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
            # Get sandbox from SandboxManager using session-specific config
            sandbox = get_sandbox_from_context(tool_context, "main")

            # Read the file content from the container using sandbox
            file_content = sandbox.extract_file_from_container(path)
            # Extract the function code using the start and end lines
            lines = file_content.split("\n")
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


@requires_sandbox("neo4j", "codeql", "joern")
async def get_caller_by_funcname(
    function_name: str, *, tool_context: ToolContext
) -> dict:
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
    # Use analysis client for static analysis queries
    client = await get_neo4j_client_from_context(tool_context, "analysis")
    params = {"function_name": function_name}
    results = await client.run_query(direct_query, params)

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
    results = await client.run_query(indirect_query, params)

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


@requires_sandbox("neo4j", "codeql", "joern")
async def get_callee_by_funcname(
    function_name: str, *, tool_context: ToolContext
) -> dict:
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
    # Use analysis client for static analysis queries
    client = await get_neo4j_client_from_context(tool_context, "analysis")
    params = {"function_name": function_name}
    results = await client.run_query(direct_query, params)

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
    results = await client.run_query(direct_query, params)
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
    results = await client.run_query(indirect_query, params)
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


@requires_sandbox("neo4j", "codeql", "joern")
async def get_caller_by_funcname_and_filepath(
    function_name: str, filepath: str, *, tool_context: ToolContext
) -> dict:
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
    # Use analysis client for static analysis queries
    client = await get_neo4j_client_from_context(tool_context, "analysis")
    params = {"function_name": function_name, "filepath": filepath}
    results = await client.run_query(direct_query, params)

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
    results = await client.run_query(indirect_query, params)

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


@requires_sandbox("neo4j", "codeql", "joern")
async def get_callee_by_funcname_and_filepath(
    function_name: str, filepath: str, *, tool_context: ToolContext
) -> dict:
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
    # Use analysis client for static analysis queries
    client = await get_neo4j_client_from_context(tool_context, "analysis")
    params = {"function_name": function_name, "filepath": filepath}
    results = await client.run_query(direct_query, params)

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
    results = await client.run_query(indirect_query, params)

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


@requires_sandbox("neo4j", "codeql", "joern")
async def get_shortest_paths_in_callgraph_to_function_in_file(
    function_name: str, filepath: str, *, tool_context: ToolContext
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
    # Use analysis client for static analysis queries
    client = await get_neo4j_client_from_context(tool_context, "analysis")
    params = {"function_name": function_name, "filepath": filepath}
    results = await client.run_query(query, params)

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
