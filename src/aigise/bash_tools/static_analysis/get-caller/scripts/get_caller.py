import argparse
import os
import sys
from typing import Optional

# Setup path to import common_utils
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(current_dir))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from common_utils.neo4j_utils import Neo4jUtils, add_neo4j_args


def _get_caller_helper(client, function_name: str, file_path: Optional[str]) -> dict:
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
    results = client.run_query(query_direct, params)

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
    results = client.run_query(query_indirect, params)

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


def main():
    parser = argparse.ArgumentParser(description="Get callers of a function.")
    parser.add_argument("function_name", help="The name of the function")
    parser.add_argument("--file-path", help="Optional file path filter")

    add_neo4j_args(parser)

    args = parser.parse_args()
    args.neo4j_database = "analysis"
    function_name = args.function_name
    file_path = args.file_path

    if file_path and os.path.isabs(file_path):
        print(
            "Error: The input file path is a full path, you should convert it to a relative path.",
            file=sys.stderr,
        )
        sys.exit(1)

    if "::" in function_name:
        function_name = function_name.split("::")[-1]

    try:
        client = Neo4jUtils.get_client_from_args(args)

        result = _get_caller_helper(client, function_name, file_path)

        # Output as plain text
        if not result["result"]:
            print(f"No callers found for function '{function_name}'.")
        else:
            print(
                f"Found {len(result['result'])} caller(s) for function '{function_name}':\n"
            )
            for i, caller in enumerate(result["result"], 1):
                print(f"Caller {i}:")
                print(f"  Function: {caller['function_name']}")
                print(f"  File: {caller['file_path']}")
                print(f"  Lines: {caller['start_line']}-{caller['end_line']}")
                print(f"  Type: {caller['call_type']}")
                print()
        client.close()

    except Exception as e:
        print(f"Error: Failed to get callers: {str(e)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
