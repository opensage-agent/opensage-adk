import json
import os
from typing import Optional

from google.adk.tools.tool_context import ToolContext

from aigise.session.neo4j_client import AsyncNeo4jClient
from aigise.toolbox.sandbox_requirements import requires_sandbox
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
        function_name (str): The name of the function to search for. Do not include the class name if the function is a method in a class. E.g. if the function name is "MyClass::myMethod", do not include "MyClass" in the function_name, only include "myMethod".
    Returns:
        dict: A dictionary containing function details if found
            (including file path, start line, end line, and first several lines of code),
            else None.
    """
    if "::" in function_name:
        function_name = function_name.split("::")[-1]
    client = await get_neo4j_client_from_context(tool_context, "analysis")

    results = await client.run_query(
        "MATCH (m:METHOD) WHERE m.name = $name "
        "RETURN m.filename as path, m.lineNumber as start,"
        "m.lineNumberEnd as end, m.code as code",
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
            "WHERE n.name = $name AND "
            "(n.filename CONTAINS $file_path OR $file_path CONTAINS n.filename)"
        )
        params = {"name": function_name, "file_path": file_path}
    else:
        where_clause = "WHERE n.name = $name"
        params = {"name": function_name}

    # Query for direct calls
    query_direct = (
        f"MATCH (m:METHOD)-[:CG_CALL]->(n:METHOD) "
        f"{where_clause} "
        f"RETURN m.name as caller_name, m.filename as path, "
        f"m.lineNumber as start, m.lineNumberEnd as end"
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
        f"RETURN m.name as caller_name, m.filename as path, "
        f"m.lineNumber as start, m.lineNumberEnd as end"
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
            "WHERE m.name = $name AND "
            "(m.filename CONTAINS $file_path OR $file_path CONTAINS m.filename) "
            "AND NOT n.name STARTS WITH '<operator>'"
        )
        params = {"name": function_name, "file_path": file_path}
    else:
        where_clause = "WHERE m.name = $name AND NOT n.name STARTS WITH '<operator>'"
        params = {"name": function_name}

    # Query for direct calls
    query_direct = (
        f"MATCH (m:METHOD)-[:CG_CALL]->(n:METHOD) "
        f"{where_clause} "
        f"RETURN n.name as callee_name, n.filename as path, "
        f"n.lineNumber as start, n.lineNumberEnd as end"
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
        f"RETURN n.name as callee_name, n.filename as path, "
        f"n.lineNumber as start, n.lineNumberEnd as end"
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
    function_name: str, file_path: Optional[str] = None, *, tool_context: ToolContext
) -> dict:
    """
    Tool to get the caller of a function in the codebase.
    Input is a function name, output is a list of dicts containing
    the caller function name, file path, and start/end line numbers.

    Args:
        function_name (str): The name of the function to search for. Do not include the class name if the function is a method in a class. E.g. if the function name is "MyClass::myMethod", do not include "MyClass" in the function_name, only include "myMethod".
        file_path (Optional[str]): The file path where the function is defined. It can be empty,
            in which case it will match all functions with the same name.
            This should be a relative path, relative to the root of the codebase. If it is a full path, you should convert it to a relative path.
    Returns:
        dict: A dictionary with key "result" pointing to a list of caller information.
    """
    if file_path and os.path.isabs(file_path):
        return "The input file path is a full path, you should convert it to a relative path, relative to the root of the codebase."
    if "::" in function_name:
        function_name = function_name.split("::")[-1]
    client = await get_neo4j_client_from_context(tool_context, "analysis")
    result = await _get_caller_helper(client, function_name, file_path)
    return result


@requires_sandbox("neo4j", "codeql", "joern")
async def get_callee(
    function_name: str, file_path: Optional[str] = None, *, tool_context: ToolContext
) -> dict:
    """
    Tool to get the callee of a function in the codebase by function name and file path.
    Args:
        function_name (str): The name of the function to search for. Do not include the class name if the function is a method in a class. E.g. if the function name is "MyClass::myMethod", do not include "MyClass" in the function_name, only include "myMethod".
        file_path (Optional[str]): The file path where the function is defined. It can be empty,
            in which case it will match all functions with the same name.
            This should be a relative path, relative to the root of the codebase. If it is a full path, you should convert it to a relative path.
    Returns:
        dict: A dictionary with key "result" pointing to a list of callee information.
    """
    if file_path and os.path.isabs(file_path):
        return "The input file path is a full path, you should convert it to a relative path, relative to the root of the codebase."
    if "::" in function_name:
        function_name = function_name.split("::")[-1]
    client = await get_neo4j_client_from_context(tool_context, "analysis")
    result = await _get_callee_helper(client, function_name, file_path)
    return result


@requires_sandbox("neo4j", "codeql", "joern")
async def get_call_paths_to_function(
    dst_function_name: str,
    dst_file_path: Optional[str] = None,
    src_function_name: Optional[str] = None,
    src_file_path: Optional[str] = None,
    *,
    tool_context: ToolContext,
) -> dict:
    """
    Get a path in the call graph from a source function to a specified destination function in the codebase.
    Note that LLVMFuzzerTestOneInput is the default source function name if not provided, it may not exist in the codebase or not captured by the call graph.

    Args:
        dst_function_name (str): The name of the destination function to search for. Do not include the class name if the function is a method in a class. E.g. if the function name is "MyClass::myMethod", do not include "MyClass" in the function_name, only include "myMethod".
        dst_file_path (Optional[str]): The file path where the destination function is defined.
            It can be empty, in which case it will match all functions with the same name. This should be a relative path, relative to the root of the codebase.
        src_function_name (Optional[str]): The name of the source function to search for. Do not include the class name if the function is a method in a class. E.g. if the function name is "MyClass::myMethod", do not include "MyClass" in the function_name, only include "myMethod".
            It can be empty, in which case it will match "LLVMFuzzerTestOneInput" by default.
        src_file_path (Optional[str]): The file path where the source function is defined.
            It can be empty, in which case it will match all functions with the same name. This should be a relative path, relative to the root of the codebase.

    Returns:
        dict: A dictionary with key "result" pointing to a list of path information.
    """
    if dst_file_path and os.path.isabs(dst_file_path):
        return "The input file path is a full path, you should convert it to a relative path, relative to the root of the codebase."
    if src_file_path and os.path.isabs(src_file_path):
        return "The input file path is a full path, you should convert it to a relative path, relative to the root of the codebase."
    if "::" in dst_function_name:
        dst_function_name = dst_function_name.split("::")[-1]
    # Default source function name to LLVMFuzzerTestOneInput if not provided
    if not src_function_name:
        src_function_name = "LLVMFuzzerTestOneInput"
    if "::" in src_function_name:
        src_function_name = src_function_name.split("::")[-1]
    client = await get_neo4j_client_from_context(tool_context, "analysis")

    dict_result = {"result": []}

    # Build the WHERE clauses based on whether file paths are provided
    if dst_file_path:
        dst_where_clause = (
            "end.name = $dst_name AND "
            "(end.filename CONTAINS $dst_file_path OR $dst_file_path CONTAINS end.filename)"
        )
    else:
        dst_where_clause = "end.name = $dst_name"

    if src_file_path:
        src_where_clause = (
            "start.name = $src_name AND "
            "(start.filename CONTAINS $src_file_path OR $src_file_path CONTAINS start.filename)"
        )
    else:
        src_where_clause = "start.name = $src_name"

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
        f"  start.name AS start_name, "
        f"  start.filename AS start_path, "
        f"  [n IN nodes(p) | {{"
        f"     name: n.name, "
        f"     path: n.filename, "
        f"     start: n.lineNumber, "
        f"     end: n.lineNumberEnd"
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
