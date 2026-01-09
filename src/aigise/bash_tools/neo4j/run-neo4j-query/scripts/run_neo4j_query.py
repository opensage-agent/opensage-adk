#!/usr/bin/env python3
"""Standalone Neo4j query runner.

Runs an arbitrary Cypher query against Neo4j and prints JSON results.

Connection discovery:
- Prefer env vars: NEO4J_HOST, NEO4J_PORT, NEO4J_USER, NEO4J_PASSWORD
- Best-effort parse /shared/bashrc for exported vars if env vars are missing.
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


def _require_env(name: str) -> str:
    val = os.environ.get(name, "")
    if not val:
        raise ValueError(f"Missing env var: {name}")
    return val


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Run an arbitrary Cypher query against Neo4j (JSON output)."
    )
    parser.add_argument("query", help="Cypher query string.")
    parser.add_argument(
        "--params",
        default="{}",
        help="JSON object string of parameters (default: {}).",
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("NEO4J_DATABASE", "analysis"),
        help="Neo4j database (default: env NEO4J_DATABASE or 'analysis').",
    )
    args = parser.parse_args(argv)

    _maybe_load_env_from_shared_bashrc()

    try:
        params = json.loads(args.params)
        if not isinstance(params, dict):
            raise ValueError("--params must be a JSON object")
    except Exception as exc:  # pylint: disable=broad-except
        print(
            json.dumps({"records": [], "error": f"Invalid --params JSON: {exc}"}),
            file=sys.stdout,
        )
        return 1

    try:
        # Import lazily so script can still print a JSON error if deps are missing.
        from neo4j import GraphDatabase  # pylint: disable=import-error
    except Exception as exc:  # pylint: disable=broad-except
        print(json.dumps({"records": [], "error": f"neo4j import failed: {exc}"}))
        return 1

    try:
        host = _require_env("NEO4J_HOST")
        port = int(_require_env("NEO4J_PORT"))
        user = _require_env("NEO4J_USER")
        password = _require_env("NEO4J_PASSWORD")
    except Exception as exc:  # pylint: disable=broad-except
        print(json.dumps({"records": [], "error": str(exc)}))
        return 1

    uri = f"neo4j://{host}:{port}"

    driver = None
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        try:
            driver.verify_connectivity()
        except Exception as exc:  # pylint: disable=broad-except
            _warn(f"Neo4j connectivity check failed: {exc}")

        with driver.session(database=args.database) as session:
            result = session.run(args.query, params)
            records = [record.data() for record in result]
        print(json.dumps({"records": records}))
        return 0
    except Exception as exc:  # pylint: disable=broad-except
        print(json.dumps({"records": [], "error": f"Query failed: {exc}"}))
        return 1
    finally:
        try:
            if driver is not None:
                driver.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
