import argparse
import os
import sys

# Setup path to import common_utils
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(current_dir))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from common_utils.neo4j_utils import Neo4jUtils, add_neo4j_args


def main():
    parser = argparse.ArgumentParser(description="Run custom Neo4j query.")
    parser.add_argument("query", help="The Cypher query string")
    parser.add_argument("--params", help="JSON dict of parameters", default="{}")
    parser.add_argument("--neo4j-database", help="Neo4j Database")

    add_neo4j_args(parser)

    args = parser.parse_args()
    query = args.query
    try:
        import json

        params = json.loads(args.params)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Error: Invalid JSON for params: {str(e)}", file=sys.stderr)
        sys.exit(1)

    try:
        client = Neo4jUtils.get_client_from_args(args)

        results = client.run_query(query, params)

        # Output results as plain text
        if not results:
            print("No results found.")
        else:
            print(f"Found {len(results)} result(s):\n")
            for i, record in enumerate(results, 1):
                print(f"Result {i}:")
                for key, value in record.items():
                    print(f"  {key}: {value}")
                print()
        client.close()

    except Exception as e:
        print(f"Error: Failed to execute query: {str(e)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
