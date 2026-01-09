#!/usr/bin/env python3
"""Standalone uploader: upload LLVM coverage for one testcase into Neo4j.

This script is designed to run inside the sandbox (from /bash_tools) without
any ADK ToolContext. It reads:
  /shared/.aigise/coverage/<2>/<2>/<testcase_id>/testcase.json

and writes coverage edges into Neo4j using the (TESTCASE)-[:COVERS]->(METHOD)
schema used by AIgiSE runtime tools.

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
        Path("/shared/.aigise/coverage")
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

    # Neo4j env vars should be provided by Neo4jInitializer via /shared/bashrc.
    host = os.environ.get("NEO4J_HOST")
    port = os.environ.get("NEO4J_PORT")
    user = os.environ.get("NEO4J_USER")
    password = os.environ.get("NEO4J_PASSWORD")

    if not host or not port or not user or not password:
        _warn(
            "Neo4j env vars not set (need NEO4J_HOST/NEO4J_PORT/NEO4J_USER/NEO4J_PASSWORD). Skipping upload."
        )
        return 0

    try:
        port_int = int(port)
    except ValueError:
        _warn(f"Invalid NEO4J_PORT value: {port!r}. Skipping upload.")
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

    try:
        client = Neo4jUtils.create_client(
            host=host,
            port=port_int,
            user=user,
            password=password,
            database=args.database,
        )
        if not client.verify_connection():
            _warn("Neo4j connectivity check failed. Skipping upload.")
            return 0
    except Exception as exc:  # pylint: disable=broad-except
        _warn(f"Failed to create Neo4j client: {exc}. Skipping upload.")
        return 0

    uploaded = 0
    try:
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
                    "MATCH (m:METHOD) WHERE m.NAME = $name "
                    "AND (m.FILENAME CONTAINS $filepath OR $filepath CONTAINS m.FILENAME) "
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

#!/usr/bin/env python3
"""Upload LLVM coverage for a testcase to Neo4j (standalone, runs in sandbox).

Design constraints:
- Must be runnable inside the sandbox without ADK context.
- Uses msgspec models to parse llvm-cov JSON.
- Uses a sync Neo4j driver, reading connection info from environment variables.
- If Neo4j is not reachable or env vars are missing, we only warn and exit 0.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _warn(msg: str) -> None:
    print(f"WARN: {msg}", file=sys.stderr)


def _add_common_utils_to_syspath() -> None:
    # /bash_tools/coverage/run-coverage/scripts/upload_coverage_to_neo4j.py
    # parents[2] => /bash_tools/coverage
    coverage_root = Path(__file__).resolve().parents[2]
    common_utils = coverage_root / "common_utils"
    sys.path.insert(0, str(common_utils))


def _read_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _get_neo4j_params() -> tuple[str, int, str, str, str] | None:
    host = _read_env("NEO4J_HOST")
    port_str = _read_env("NEO4J_PORT")
    user = _read_env("NEO4J_USER")
    password = _read_env("NEO4J_PASSWORD")
    database = _read_env("NEO4J_DATABASE") or "analysis"

    missing = [
        k
        for k, v in (
            ("NEO4J_HOST", host),
            ("NEO4J_USER", user),
            ("NEO4J_PASSWORD", password),
        )
        if not v
    ]
    if missing:
        _warn(f"Neo4j env vars missing ({', '.join(missing)}); skipping upload.")
        return None

    port = 7687
    if port_str:
        try:
            port = int(port_str)
        except ValueError:
            _warn(f"Invalid NEO4J_PORT={port_str!r}; using default 7687.")
            port = 7687

    return host, port, user, password, database


def _coverage_dir_for_testcase(testcase_id: str) -> str:
    return (
        f"/shared/.aigise/coverage/{testcase_id[:2]}/{testcase_id[2:4]}/{testcase_id}"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--testcase-id", required=True, help="MD5 testcase id")
    args = parser.parse_args(argv)

    testcase_id = args.testcase_id.strip()
    if len(testcase_id) != 32:
        _warn(
            f"Invalid testcase id (expected 32-char md5): {testcase_id!r}; skipping upload."
        )
        return 0

    _add_common_utils_to_syspath()
    try:
        import llvm_cov  # type: ignore
        import neo4j_utils  # type: ignore
    except Exception as exc:  # pylint: disable=broad-except
        _warn(
            f"Failed to import coverage common_utils modules: {exc}; skipping upload."
        )
        return 0

    neo = _get_neo4j_params()
    if neo is None:
        return 0
    host, port, user, password, database = neo

    testcase_dir = _coverage_dir_for_testcase(testcase_id)
    json_path = f"{testcase_dir}/testcase.json"
    try:
        json_bytes = Path(json_path).read_bytes()
    except Exception as exc:  # pylint: disable=broad-except
        _warn(f"Failed to read coverage json at {json_path}: {exc}; skipping upload.")
        return 0

    try:
        cov = llvm_cov.parse_llvm_coverage_json(json_bytes)
    except Exception as exc:  # pylint: disable=broad-except
        _warn(
            f"Failed to parse LLVM coverage JSON ({json_path}): {exc}; skipping upload."
        )
        return 0

    try:
        client = neo4j_utils.SyncNeo4jClient(
            f"neo4j://{host}:{port}",
            user,
            password,
            database=database,
        )
        if not client.verify_connection():
            _warn(
                f"Neo4j not reachable at {host}:{port} (db={database}); skipping upload."
            )
            return 0
    except Exception as exc:  # pylint: disable=broad-except
        _warn(
            f"Failed to connect to Neo4j at {host}:{port} (db={database}): {exc}; skipping upload."
        )
        return 0

    uploaded = 0
    try:
        export = cov.data[0]
        for func in export.functions:
            # Match existing METHOD nodes by name + filename containment.
            name = func.name.split(":")[-1]
            filepath = func.filenames[0] if func.filenames else ""
            if not filepath:
                continue

            result = client.run_query(
                "MATCH (m:METHOD) WHERE m.NAME = $name "
                "AND (m.FILENAME CONTAINS $filepath OR $filepath CONTAINS m.FILENAME) "
                "RETURN m.id",
                {"name": name, "filepath": filepath},
            )
            if not result or len(result) != 1:
                continue
            method_id = result[0].get("m.id")
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
    except Exception as exc:  # pylint: disable=broad-except
        _warn(f"Neo4j upload failed: {exc}; skipping.")
        return 0
    finally:
        try:
            client.close()
        except Exception:
            pass

    print(f"Uploaded coverage: testcase_id={testcase_id}, edges={uploaded}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
