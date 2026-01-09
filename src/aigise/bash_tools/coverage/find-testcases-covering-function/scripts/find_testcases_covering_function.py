#!/usr/bin/env python3
"""Standalone Neo4j query: find testcase IDs covering a function.

This script mirrors the behavior of the Python tool:
  aigise.toolbox.coverage.tools.find_testcases_covering_function

But runs as a standalone bash_tool script inside the sandbox.

Neo4j connection:
  - Prefer env vars: NEO4J_HOST, NEO4J_PORT, NEO4J_USER, NEO4J_PASSWORD
  - If missing, best-effort parse /shared/bashrc for exported vars.

Failure policy:
  - If Neo4j is not configured or unreachable, print WARN to stderr and return
    {"testcase_ids": []} with exit code 0.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


def _warn(msg: str) -> None:
    print(f"WARN: {msg}", file=sys.stderr)


def _maybe_load_env_from_shared_bashrc() -> None:
    """Parse /shared/bashrc for 'export KEY=VALUE' lines and set os.environ."""
    bashrc = Path("/shared/bashrc")
    if not bashrc.exists():
        return
    try:
        text = bashrc.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return

    pattern = re.compile(r"^\s*export\s+(NEO4J_[A-Z0-9_]+)=(.+?)\s*$")
    for line in text.splitlines():
        m = pattern.match(line)
        if not m:
            continue
        key = m.group(1)
        raw_val = m.group(2).strip()
        # Strip simple quotes.
        if (raw_val.startswith("'") and raw_val.endswith("'")) or (
            raw_val.startswith('"') and raw_val.endswith('"')
        ):
            raw_val = raw_val[1:-1]
        os.environ.setdefault(key, raw_val)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("function_name", help="Function name to query (m.NAME).")
    parser.add_argument(
        "--file_path",
        default="",
        help="Optional file path to disambiguate (substring match against m.FILENAME).",
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("NEO4J_DATABASE", "analysis"),
        help="Neo4j database (default: env NEO4J_DATABASE or 'analysis').",
    )
    args = parser.parse_args(argv)

    _maybe_load_env_from_shared_bashrc()

    # Ensure we can import coverage common_utils when running from /bash_tools.
    # File path: .../coverage/find-testcases-covering-function/scripts/...
    coverage_root = Path(__file__).resolve().parents[3]  # .../coverage
    sys.path.insert(0, str(coverage_root))

    try:
        from common_utils.neo4j_utils import Neo4jUtils  # pylint: disable=import-error
    except Exception as exc:  # pylint: disable=broad-except
        _warn(f"Failed to import Neo4j utils: {exc}")
        print(json.dumps({"testcase_ids": []}))
        return 0

    host = os.environ.get("NEO4J_HOST")
    port = os.environ.get("NEO4J_PORT")
    user = os.environ.get("NEO4J_USER")
    password = os.environ.get("NEO4J_PASSWORD")

    if not host or not port or not user or not password:
        _warn(
            "Neo4j env vars not set (need NEO4J_HOST/NEO4J_PORT/NEO4J_USER/NEO4J_PASSWORD)."
        )
        print(json.dumps({"testcase_ids": []}))
        return 0

    try:
        port_int = int(port)
    except ValueError:
        _warn(f"Invalid NEO4J_PORT: {port!r}")
        print(json.dumps({"testcase_ids": []}))
        return 0

    try:
        client = Neo4jUtils.create_client(host, port_int, user, password, args.database)
        if not client.verify_connection():
            _warn("Neo4j connectivity check failed.")
            print(json.dumps({"testcase_ids": []}))
            return 0
    except Exception as exc:  # pylint: disable=broad-except
        _warn(f"Failed to connect to Neo4j: {exc}")
        print(json.dumps({"testcase_ids": []}))
        return 0

    try:
        query = "MATCH (t:TESTCASE)-[c:COVERS]->(m:METHOD) WHERE m.NAME = $name "
        params = {"name": args.function_name}
        if args.file_path:
            query += (
                "AND (m.FILENAME CONTAINS $filepath OR $filepath CONTAINS m.FILENAME) "
            )
            params["filepath"] = args.file_path
        query += "RETURN t.id AS testcase_id"

        results = client.run_query(query, params)
        testcase_ids = []
        for record in results or []:
            val = record.get("testcase_id")
            if val:
                testcase_ids.append(val)
        print(json.dumps({"testcase_ids": testcase_ids}))
        return 0
    except Exception as exc:  # pylint: disable=broad-except
        _warn(f"Query failed: {exc}")
        print(json.dumps({"testcase_ids": []}))
        return 0
    finally:
        try:
            client.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
