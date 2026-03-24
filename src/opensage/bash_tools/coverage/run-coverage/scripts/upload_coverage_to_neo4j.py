#!/usr/bin/env python3
"""Standalone uploader: upload LLVM coverage for one testcase into Neo4j.

This script is designed to run inside the sandbox (from /bash_tools) without
any ADK ToolContext. It reads:
  /shared/.opensage/coverage/<2>/<2>/<testcase_id>/testcase.json

and writes coverage edges into Neo4j using the (TESTCASE)-[:COVERS]->(METHOD)
schema used by OpenSage runtime tools.

Failure policy:
  - If Neo4j env vars are missing OR Neo4j is unreachable, prints WARN and exits
    with code 0 (coverage remains successful).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _warn(msg: str) -> None:
    print(f"WARN: {msg}", file=sys.stderr)


def _coverage_dir_for_testcase_id(testcase_id: str) -> Path:
    if len(testcase_id) != 32:
        raise ValueError(f"Invalid testcase_id length: {testcase_id!r}")
    return (
        Path("/shared/.opensage/coverage")
        / testcase_id[:2]
        / testcase_id[2:4]
        / testcase_id
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--testcase-id", required=True, help="MD5 testcase id (32 hex chars)."
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("NEO4J_DATABASE", "analysis"),
        help="Neo4j database name (default: env NEO4J_DATABASE or 'analysis').",
    )
    args = parser.parse_args(argv)
    args.neo4j_database = "analysis"

    # Ensure we can import coverage common_utils when running from /bash_tools.
    # File path: .../coverage/run-coverage/scripts/upload_coverage_to_neo4j.py
    # parents[2] == .../coverage
    coverage_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(coverage_root))

    try:
        from common_utils.llvm_cov import (
            parse_llvm_coverage_json,  # pylint: disable=import-error
        )
        from common_utils.neo4j_utils import Neo4jUtils  # pylint: disable=import-error
    except Exception as exc:  # pylint: disable=broad-except
        _warn(f"Failed to import coverage helpers: {exc}")
        return 0

    testcase_id = args.testcase_id.strip()
    try:
        cov_dir = _coverage_dir_for_testcase_id(testcase_id)
    except ValueError as exc:
        _warn(str(exc))
        return 0

    cov_json_path = cov_dir / "testcase.json"
    if not cov_json_path.exists():
        _warn(f"Coverage JSON not found: {cov_json_path}. Skipping upload.")
        return 0

    try:
        cov_bytes = cov_json_path.read_bytes()
        cov = parse_llvm_coverage_json(cov_bytes)
    except Exception as exc:  # pylint: disable=broad-except
        _warn(f"Failed to parse coverage JSON {cov_json_path}: {exc}")
        return 0

    uploaded = 0
    try:
        client = Neo4jUtils.get_client_from_args(args)
        exports = cov.data or []
        if not exports:
            _warn("Coverage JSON has no exports. Skipping upload.")
            return 0

        # We only need function coverage info for TESTCASE->METHOD edges.
        for func in exports[0].functions:
            try:
                func_name = func.name.split(":")[-1]
                filepath = func.filenames[0] if func.filenames else ""
                if not filepath:
                    continue
                match_res = client.run_query(
                    "MATCH (m:METHOD) WHERE m.name = $name "
                    "AND (m.filename CONTAINS $filepath OR $filepath CONTAINS m.filename) "
                    "RETURN m.id",
                    {"name": func_name, "filepath": filepath},
                )
                if not match_res or len(match_res) != 1:
                    continue
                method_id = match_res[0].get("m.id")
                if not method_id:
                    continue
                client.run_query(
                    "MATCH (m:METHOD {id: $method_id}) "
                    "MERGE (t:TESTCASE {id: $testcase_id}) "
                    "MERGE (t)-[c:COVERS]->(m) "
                    "SET c.count = $count",
                    {
                        "testcase_id": testcase_id,
                        "method_id": method_id,
                        "count": func.count,
                    },
                )
                uploaded += 1
            except Exception:
                # Best-effort per function; never fail the whole script.
                continue
    finally:
        try:
            client.close()
        except Exception:
            pass

    print(f"Uploaded coverage edges: {uploaded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
