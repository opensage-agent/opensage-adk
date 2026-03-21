import argparse
import os
import sys

# Add src to sys.path to allow importing from opensage tree if needed
# Also add common_utils path
current_dir = os.path.dirname(os.path.abspath(__file__))
# static_analysis/search-function/scripts -> static_analysis
parent_dir = os.path.dirname(os.path.dirname(current_dir))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from common_utils.neo4j_utils import Neo4jUtils, add_neo4j_args


def main():
    parser = argparse.ArgumentParser(
        description="Search for a function in the codebase."
    )
    parser.add_argument("function_name", help="The name of the function to search for")

    # Add standard Neo4j args
    add_neo4j_args(parser)

    args = parser.parse_args()
    args.neo4j_database = "analysis"
    function_name = args.function_name

    if "::" in function_name:
        function_name = function_name.split("::")[-1]

    try:
        client = Neo4jUtils.get_client_from_args(args)

        results = client.run_query(
            "MATCH (m:METHOD) WHERE m.name = $name "
            "RETURN m.filename as path, m.lineNumber as start,"
            "m.lineNumberEnd as end, m.code as code",
            {"name": function_name},
        )

        # Output as plain text
        if not results:
            print(f"No functions found matching '{function_name}'.")
        else:
            print(f"Found {len(results)} function(s) matching '{function_name}':\n")
            for i, record in enumerate(results, 1):
                print(f"Function {i}:")
                print(f"  Name: {function_name}")
                print(f"  File: {record['path']}")
                print(f"  Lines: {record['start']}-{record['end']}")
                if record.get("code"):
                    # Truncate code if too long
                    code = record["code"]
                    if len(code) > 200:
                        code = code[:200] + "..."
                    print(f"  Code: {code}")
                print()
        client.close()

    except Exception as e:
        print(f"Error: Failed to search for function: {str(e)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
