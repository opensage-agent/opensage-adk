#!/usr/bin/env bash
set -euo pipefail

# Install deps into the main sandbox uv venv explicitly.
# Ignore failures: https://github.com/SWE-agent/SWE-agent/issues/1179
/app/.venv/bin/python -m pip install 'tree-sitter==0.21.3' || true
/app/.venv/bin/python -m pip install 'tree-sitter-languages' || true
