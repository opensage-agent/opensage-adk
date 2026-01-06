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
    parser = argparse.ArgumentParser(description="Test Neo4j connection.")
    add_neo4j_args(parser)

    args = parser.parse_args()

    try:
        # Create client manually to catch connection errors explicitly during creation if needed,
        # but get_client_from_args calls verify_connection()
        client = Neo4jUtils.get_client_from_args(args)

        # Run a simple query to verify execution capability
        results = client.run_query("RETURN 1 as val")

        print("Neo4j connection successful")
        print(f"Test query result: {results}")
        client.close()

    except Exception as e:
        print(f"Neo4j connection failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
