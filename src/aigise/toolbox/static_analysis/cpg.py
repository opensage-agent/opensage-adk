import json
from typing import Optional

from google.adk.tools.tool_context import ToolContext

from aigise.session.neo4j_client import AsyncNeo4jClient
from aigise.toolbox.decorators import requires_sandbox
from aigise.utils.agent_utils import (
    get_joern_client_from_context,
    get_neo4j_client_from_context,
    get_sandbox_from_context,
)


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
    client: AsyncNeo4jClient, function_name: str, file_path: Optional[str]
) -> dict:
    """
    Helper function to get callers of a function, with optional file_path filtering.

    Args:
        client: The Neo4j client instance.
        function_name (str): The name of the function to search for.
        file_path (Optional[str]): Optional file path to filter results. If provided,
            only callers to functions in the specified file are returned.

    Returns:
        dict: A dictionary with key "result" pointing to a list of caller information.
    """
    dict_result = {"result": []}

    # Build the WHERE clause based on whether file_path is provided
    if file_path:
        where_clause = (
            "WHERE n.NAME = $name AND "
            "(n.FILENAME CONTAINS $file_path OR $file_path CONTAINS n.FILENAME)"
        )
        params = {"name": function_name, "file_path": file_path}
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
    client: AsyncNeo4jClient, function_name: str, file_path: Optional[str]
) -> dict:
    """
    Helper function to get callees of a function, with optional file_path filtering.

    Args:
        client: The Neo4j client instance.
        function_name (str): The name of the function to search for.
        file_path (Optional[str]): Optional file path to filter results. If provided,
            only callees from functions in the specified file are returned.

    Returns:
        dict: A dictionary with key "result" pointing to a list of callee information.
    """
    dict_result = {"result": []}

    # Build the WHERE clause based on whether file_path is provided
    if file_path:
        where_clause = (
            "WHERE m.NAME = $name AND "
            "(m.FILENAME CONTAINS $file_path OR $file_path CONTAINS m.FILENAME) "
            "AND NOT n.NAME STARTS WITH '<operator>'"
        )
        params = {"name": function_name, "file_path": file_path}
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
async def get_caller(
    function_name: str, file_path: Optional[str], *, tool_context: ToolContext
) -> dict:
    """
    Tool to get the caller of a function in the codebase.
    Input is a function name, output is a list of dicts containing
    the caller function name, file path, and start/end line numbers.

    Args:
        function_name (str): The name of the function to search for.
        file_path (Optional[str]): The file path where the function is defined. It can be empty,
            in which case it will match all functions with the same name.
    Returns:
        dict: A dictionary with key "result" pointing to a list of caller information.
    """
    client = await get_neo4j_client_from_context(tool_context, "analysis")
    return await _get_caller_helper(client, function_name, file_path)


@requires_sandbox("neo4j", "codeql", "joern")
async def get_callee(
    function_name: str, file_path: Optional[str], *, tool_context: ToolContext
) -> dict:
    """
    Tool to get the callee of a function in the codebase by function name and file path.
    Args:
        function_name (str): The name of the function to search for.
        file_path (Optional[str]): The file path where the function is defined. It can be empty,
            in which case it will match all functions with the same name.
    Returns:
        dict: A dictionary with key "result" pointing to a list of callee information.
    """
    client = await get_neo4j_client_from_context(tool_context, "analysis")
    return await _get_callee_helper(client, function_name, file_path)


@requires_sandbox("neo4j", "codeql", "joern")
async def get_shortest_paths_in_callgraph_to_function_in_file(
    dst_function_name: str,
    dst_file_path: Optional[str],
    src_function_name: Optional[str],
    src_file_path: Optional[str],
    *,
    tool_context: ToolContext,
) -> dict:
    """
    Tool to get the shortest paths from each fuzzing entrypoint (LLVMFuzzerTestOneInput)
    to a specified end function in the codebase.

    Args:
        dst_function_name (str): The name of the destination function to search for.
        dst_file_path (Optional[str]): The file path where the destination function is defined.
            It can be empty, in which case it will match all functions with the same name.
        src_function_name (Optional[str]): The name of the source function to search for.
            It can be empty, in which case it will match "LLVMFuzzerTestOneInput" by default.
        src_file_path (Optional[str]): The file path where the source function is defined.
            It can be empty, in which case it will match all functions with the same name.

    Returns:
        dict: A dictionary with key "result" pointing to a list of path information.
    """
    client = await get_neo4j_client_from_context(tool_context, "analysis")

    dict_result = {"result": []}

    # Default source function name to LLVMFuzzerTestOneInput if not provided
    if not src_function_name:
        src_function_name = "LLVMFuzzerTestOneInput"

    # Build the WHERE clauses based on whether file paths are provided
    if dst_file_path:
        dst_where_clause = (
            "end.NAME = $dst_name AND "
            "(end.FILENAME CONTAINS $dst_file_path OR $dst_file_path CONTAINS end.FILENAME)"
        )
    else:
        dst_where_clause = "end.NAME = $dst_name"

    if src_file_path:
        src_where_clause = (
            "start.NAME = $src_name AND "
            "(start.FILENAME CONTAINS $src_file_path OR $src_file_path CONTAINS start.FILENAME)"
        )
    else:
        src_where_clause = "start.NAME = $src_name"

    # Use allShortestPaths to find all shortest paths with a reasonable depth limit
    query = (
        f"MATCH (end:METHOD) WHERE {dst_where_clause} "
        f"MATCH (start:METHOD) WHERE {src_where_clause} "
        f"WITH start, end "
        f"MATCH p = allShortestPaths("
        f"  (start)-[:CG_CALL|CG_MAYBE_INDIRECT_CALL*..10]->(end)"
        f") "
        f"WHERE p IS NOT NULL "
        f"RETURN "
        f"  start.NAME AS start_name, "
        f"  start.FILENAME AS start_path, "
        f"  [n IN nodes(p) | {{"
        f"     name: n.NAME, "
        f"     path: n.FILENAME, "
        f"     start: n.LINE_NUMBER, "
        f"     end: n.LINE_NUMBER_END"
        f"  }}] AS path_nodes "
        f"ORDER BY start_name"
    )

    params = {
        "dst_name": dst_function_name,
        "src_name": src_function_name,
    }
    if dst_file_path:
        params["dst_file_path"] = dst_file_path
    if src_file_path:
        params["src_file_path"] = src_file_path

    results = await client.run_query(query, params)

    if not results:
        return dict_result

    for record in results:
        start_name = record.get("start_name")
        start_path = record.get("start_path")
        path_nodes = record.get("path_nodes", [])

        path_info = {
            "start_function": start_name,
            "start_filepath": start_path,
            "target_function": dst_function_name,
            "target_filepath": dst_file_path,
            "path_nodes": [],
        }

        for node in path_nodes:
            path_info["path_nodes"].append(
                {
                    "function_name": node["name"],
                    "file_path": node["path"],
                    "start_line": node["start"],
                    "end_line": node["end"],
                }
            )

        dict_result["result"].append(path_info)

    return dict_result


@requires_sandbox("neo4j", "codeql", "joern")
async def neo4j_query(
    query: str, params: Optional[dict] = None, *, tool_context: ToolContext
) -> list[dict]:
    """
    Tool to run a custom Neo4j query against the code property graph.

    Args:
        query (str): The Cypher query string to execute.
        params (Optional[dict]): Optional dictionary of parameters for the query.

    Returns:
        list[dict]: A list of dictionaries representing the query results.
    """
    client = await get_neo4j_client_from_context(tool_context, "analysis")

    results = await client.run_query(query, params or {})

    return results


@requires_sandbox("joern")
async def joern_slice(
    function_name: str, file_path: Optional[str], *, tool_context: ToolContext
):
    """
    Tool to get the program slice for a given function using Joern.

    Args:
        function_name (str): The name of the function to slice.
        file_path (Optional[str]): The file path where the function is defined.

    Returns:
        The response from the Joern client.
    """
    sandbox = get_sandbox_from_context(tool_context, "joern")
    sandbox.run_command_in_container(
        [
            "joern-slice",
            "data-flow",
            "-o",
            "/tmp/slices.json",
            "--method-name-filter",
            function_name,
        ]
        + (["--file-filter", file_path] if file_path else [])
        + ["/cpg.bin"]
    )
    res = json.loads(sandbox.extract_file_from_container("/tmp/slices.json"))
    nodes = res["nodes"]
    lines = {}
    for node in nodes:
        fp = node["parentFile"]
        if fp not in lines:
            lines[fp] = set()
        lines[fp].add(node["lineNumber"])
    slice_result = {"result": []}
    # TODO: fetch the code lines as well
    for fp, line_set in lines.items():
        slice_result["result"].append(
            {
                "file_path": fp,
                "lines": sorted(list(line_set)),
            }
        )
    return slice_result


@requires_sandbox("joern")
async def joern_query(query: str, *, tool_context: ToolContext):
    """
    Tool to run a custom Joern query against the code property graph.

    Args:
        query (str): The Joern query string to execute.

    Returns:
        The response from the Joern client.
    """
    client = await get_joern_client_from_context(tool_context)

    response = await client.aexecute(query)

    return response
