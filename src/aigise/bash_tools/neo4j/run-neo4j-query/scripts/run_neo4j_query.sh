#!/usr/bin/env bash
set -euo pipefail

# Wrapper to run the Python implementation with the system python.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "${SCRIPT_DIR}/run_neo4j_query.py" "$@"
