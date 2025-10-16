from typing import Optional

from google.adk.tools.tool_context import ToolContext

from aigise.session.neo4j_client import AsyncNeo4jClient
from aigise.toolbox.decorators import requires_sandbox
from aigise.utils.agent_utils import get_neo4j_client_from_context


@requires_sandbox("neo4j", "codeql", "joern")
async def search_function(function_name: str, *, tool_context: ToolContext) -> dict:
    """
    Tool to search for a function in the codebase.
    Input is a function name, output is a dictionary containing the implementation of the function.
    Args:
        function_name (str): The name of the function to search for.
    Returns:
        dict: A dictionary containing function details if found
            (including file path, start line, end line, and first several lines of code),
            else None.
    """
    client = await get_neo4j_client_from_context(tool_context, "analysis")

    results = await client.run_query(
        "MATCH (m:METHOD) WHERE m.NAME = $name "
        "RETURN m.FILENAME as path, m.LINE_NUMBER as start,"
        "m.LINE_NUMBER_END as end, m.CODE as code",
        {"name": function_name},
    )

    dict_result = {"result": []}

    for record in results:
        dict_result["result"].append(
            {
                "function_name": function_name,
                "file_path": record["path"],
                "start_line": record["start"],
                "end_line": record["end"],
                "code": record["code"],
            }
        )

    return dict_result


async def _get_caller_helper(
    client: AsyncNeo4jClient, function_name: str, filepath: Optional[str]
) -> dict:
    """
    Helper function to get callers of a function, with optional filepath filtering.

    Args:
        client: The Neo4j client instance.
        function_name (str): The name of the function to search for.
        filepath (Optional[str]): Optional file path to filter results. If provided,
            only callers to functions in the specified file are returned.

    Returns:
        dict: A dictionary with key "result" pointing to a list of caller information.
    """
    dict_result = {"result": []}

    # Build the WHERE clause based on whether filepath is provided
    if filepath:
        where_clause = (
            "WHERE n.NAME = $name AND "
            "(n.FILENAME CONTAINS $filepath OR $filepath CONTAINS n.FILENAME)"
        )
        params = {"name": function_name, "filepath": filepath}
    else:
        where_clause = "WHERE n.NAME = $name"
        params = {"name": function_name}

    # Query for direct calls
    query_direct = (
        f"MATCH (m:METHOD)-[:CG_CALL]->(n:METHOD) "
        f"{where_clause} "
        f"RETURN m.NAME as caller_name, m.FILENAME as path, "
        f"m.LINE_NUMBER as start, m.LINE_NUMBER_END as end"
    )
    results = await client.run_query(query_direct, params)

    for record in results:
        dict_result["result"].append(
            {
                "function_name": record["caller_name"],
                "file_path": record["path"],
                "start_line": record["start"],
                "end_line": record["end"],
                "call_type": "direct",
            }
        )

    # Query for indirect calls
    query_indirect = (
        f"MATCH (m:METHOD)-[:CG_MAYBE_INDIRECT_CALL]->(n:METHOD) "
        f"{where_clause} "
        f"RETURN m.NAME as caller_name, m.FILENAME as path, "
        f"m.LINE_NUMBER as start, m.LINE_NUMBER_END as end"
    )
    results = await client.run_query(query_indirect, params)

    for record in results:
        dict_result["result"].append(
            {
                "function_name": record["caller_name"],
                "file_path": record["path"],
                "start_line": record["start"],
                "end_line": record["end"],
                "call_type": "indirect",
            }
        )

    return dict_result


async def _get_callee_helper(
    client: AsyncNeo4jClient, function_name: str, filepath: Optional[str]
) -> dict:
    """
    Helper function to get callees of a function, with optional filepath filtering.

    Args:
        client: The Neo4j client instance.
        function_name (str): The name of the function to search for.
        filepath (Optional[str]): Optional file path to filter results. If provided,
            only callees from functions in the specified file are returned.

    Returns:
        dict: A dictionary with key "result" pointing to a list of callee information.
    """
    dict_result = {"result": []}

    # Build the WHERE clause based on whether filepath is provided
    if filepath:
        where_clause = (
            "WHERE m.NAME = $name AND "
            "(m.FILENAME CONTAINS $filepath OR $filepath CONTAINS m.FILENAME) "
            "AND NOT n.NAME STARTS WITH '<operator>'"
        )
        params = {"name": function_name, "filepath": filepath}
    else:
        where_clause = "WHERE m.NAME = $name AND NOT n.NAME STARTS WITH '<operator>'"
        params = {"name": function_name}

    # Query for direct calls
    query_direct = (
        f"MATCH (m:METHOD)-[:CG_CALL]->(n:METHOD) "
        f"{where_clause} "
        f"RETURN n.NAME as callee_name, n.FILENAME as path, "
        f"n.LINE_NUMBER as start, n.LINE_NUMBER_END as end"
    )
    results = await client.run_query(query_direct, params)

    for record in results:
        dict_result["result"].append(
            {
                "function_name": record["callee_name"],
                "file_path": record["path"],
                "start_line": record["start"],
                "end_line": record["end"],
                "call_type": "direct",
            }
        )

    # Query for indirect calls
    query_indirect = (
        f"MATCH (m:METHOD)-[:CG_MAYBE_INDIRECT_CALL]->(n:METHOD) "
        f"{where_clause} "
        f"RETURN n.NAME as callee_name, n.FILENAME as path, "
        f"n.LINE_NUMBER as start, n.LINE_NUMBER_END as end"
    )
    results = await client.run_query(query_indirect, params)

    for record in results:
        dict_result["result"].append(
            {
                "function_name": record["callee_name"],
                "file_path": record["path"],
                "start_line": record["start"],
                "end_line": record["end"],
                "call_type": "indirect",
            }
        )

    return dict_result


@requires_sandbox("neo4j", "codeql", "joern")
async def get_caller_by_funcname(
    function_name: str, *, tool_context: ToolContext
) -> dict:
    """
    Tool to get the caller of a function in the codebase.
    Input is a function name, output is a list of dicts containing
    the caller function name, file path, and start/end line numbers.

    Args:
        function_name (str): The name of the function to search for.
    Returns:
        dict: A dictionary with key "result" pointing to a list of caller information.
    """
    client = await get_neo4j_client_from_context(tool_context, "analysis")
    return await _get_caller_helper(client, function_name, None)


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
    client = await get_neo4j_client_from_context(tool_context, "analysis")
    return await _get_callee_helper(client, function_name, None)


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
    client = await get_neo4j_client_from_context(tool_context, "analysis")
    return await _get_caller_helper(client, function_name, filepath)


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
    client = await get_neo4j_client_from_context(tool_context, "analysis")
    return await _get_callee_helper(client, function_name, filepath)


# @requires_sandbox("neo4j", "codeql", "joern")
# async def get_shortest_paths_in_callgraph_to_function_in_file(
#     function_name: str, filepath: str, *, tool_context: ToolContext
# ) -> dict:
#     """
#     Tool to get the shortest paths from each fuzzing entrypoint (LLVMFuzzerTestOneInput)
#     to a specified end function in the codebase.

#     Args:
#         function_name (str): The name of the target end function.
#         filepath (str): The file path where the end function is defined.

#     Returns:
#         dict: A dictionary with key "result" pointing to a list of path information.
#     """
#     ...
