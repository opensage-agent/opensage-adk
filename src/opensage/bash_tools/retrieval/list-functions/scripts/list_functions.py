import argparse
import os
import sys

# Ensure retrieval/common_utils is importable:
# retrieval/list-functions/scripts -> retrieval
current_dir = os.path.dirname(os.path.abspath(__file__))
retrieval_dir = os.path.dirname(os.path.dirname(current_dir))

if retrieval_dir not in sys.path:
    sys.path.append(retrieval_dir)


from common_utils.neo4j_utils import Neo4jUtils, add_neo4j_args


def main():
    parser = argparse.ArgumentParser(description="List functions in a file.")
    parser.add_argument("--file", required=True, help="Relative path to the file")

    add_neo4j_args(parser)

    args = parser.parse_args()
    args.neo4j_database = "analysis"
    filepath = args.file

    if os.path.isabs(filepath):
        print(
            "Error: The input file path is an absolute path, you should convert it to a relative path, relative to the root of the codebase."
        )
        return

    try:
        client = Neo4jUtils.get_client_from_args(args)

        query = """
        MATCH (f:METHOD)
        WHERE f.filename CONTAINS $filepath OR $filepath CONTAINS f.filename
        RETURN
            f.name AS function_name,
            f.lineNumber AS start,
            f.lineNumberEnd AS end
        """
        params = {"filepath": filepath}
        results = client.run_query(query, params)

        if not results:
            print(f"No functions found in file: {filepath}")
            return

        for res in results:
            if isinstance(res, dict):
                function_name = res.get("function_name")
                start = res.get("start")
                end = res.get("end")
            else:
                # Fallback if list/tuple
                function_name = res[0]
                start = res[1]
                end = res[2]

            if not function_name:
                continue

            # Print function information in text format
            print(f"Function: {function_name}")
            print(f"  File: {filepath}")
            print(f"  Lines: {start}-{end}")
            print()

        client.close()

    except Exception as e:
        print(
            f"Error: Failed to query database for functions in '{filepath}': {str(e)}"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
