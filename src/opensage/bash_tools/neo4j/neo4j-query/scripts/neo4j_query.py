import argparse
import json
import os
import sys


def _print_json(payload: dict, *, exit_code: int) -> None:
    print(json.dumps(payload), file=sys.stdout)
    raise SystemExit(exit_code)


def main() -> None:
    # Setup path to import common_utils (neo4j/common_utils).
    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(os.path.dirname(current_dir))
    if parent_dir not in sys.path:
        sys.path.append(parent_dir)

    from common_utils.neo4j_utils import (  # pylint: disable=import-error
        Neo4jUtils,
        add_neo4j_args,
    )

    parser = argparse.ArgumentParser(
        description="Run an arbitrary Cypher query against Neo4j (JSON output)."
    )
    parser.add_argument("query", help="The Cypher query string")
    parser.add_argument(
        "--params",
        help="JSON object string of parameters (default: {}).",
        default="{}",
    )
    # Keep a human-friendly flag name, but map to common_utils' expected field.
    parser.add_argument(
        "--database",
        dest="neo4j_database",
        default=os.environ.get("NEO4J_DATABASE", "analysis"),
        help="Neo4j database (default: env NEO4J_DATABASE or 'analysis').",
    )
    add_neo4j_args(parser)

    args = parser.parse_args()

    try:
        params = json.loads(args.params)
        if not isinstance(params, dict):
            raise ValueError("--params must be a JSON object")
    except Exception as exc:  # pylint: disable=broad-except
        _print_json(
            {"records": [], "error": f"Invalid --params JSON: {exc}"}, exit_code=1
        )

    client = None
    try:
        client = Neo4jUtils.get_client_from_args(args)
        records = client.run_query(args.query, params)
        _print_json({"records": records}, exit_code=0)
    except Exception as exc:  # pylint: disable=broad-except
        _print_json({"records": [], "error": f"Query failed: {exc}"}, exit_code=1)
    finally:
        try:
            if client is not None:
                client.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
