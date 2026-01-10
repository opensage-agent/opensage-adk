import argparse
import json
import os
import sys

# Setup path to import common_utils
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(current_dir))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from common_utils.neo4j_utils import Neo4jUtils, add_neo4j_args


def main():
    parser = argparse.ArgumentParser(description="Get call paths between functions.")
    parser.add_argument("dst_function_name", help="Destination function name")
    parser.add_argument("--dst-file", help="Destination file path")
    parser.add_argument("--src-function", help="Source function name")
    parser.add_argument("--src-file", help="Source file path")

    add_neo4j_args(parser)

    args = parser.parse_args()
    args.neo4j_database = "analysis"
    dst_function_name = args.dst_function_name
    dst_file_path = args.dst_file
    src_function_name = args.src_function
    src_file_path = args.src_file

    if dst_file_path and os.path.isabs(dst_file_path):
        print(
            json.dumps(
                {
                    "error": "The input dst file path is a full path, you should convert it to a relative path."
                }
            )
        )
        return
    if src_file_path and os.path.isabs(src_file_path):
        print(
            json.dumps(
                {
                    "error": "The input src file path is a full path, you should convert it to a relative path."
                }
            )
        )
        return

    if "::" in dst_function_name:
        dst_function_name = dst_function_name.split("::")[-1]

    # Default source function name
    if not src_function_name:
        src_function_name = "LLVMFuzzerTestOneInput"

    if "::" in src_function_name:
        src_function_name = src_function_name.split("::")[-1]

    try:
        client = Neo4jUtils.get_client_from_args(args)

        dict_result = {"result": []}

        # Build the WHERE clauses
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

        results = client.run_query(query, params)

        if not results:
            print(json.dumps(dict_result))
            client.close()
            return

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

        print(json.dumps(dict_result))
        client.close()

    except Exception as e:
        print(json.dumps({"result": [], "error": str(e)}))


if __name__ == "__main__":
    main()
