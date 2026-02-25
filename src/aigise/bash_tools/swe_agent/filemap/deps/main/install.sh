#!/usr/bin/env bash
set -euo pipefail

# Ensure helper functions/env are available when present.
if [ -f /shared/bashrc ]; then
  # shellcheck disable=SC1091
  source /shared/bashrc
fi

uv pip install --python /app/.venv/bin/python 'tree-sitter==0.21.3'
uv pip install --python /app/.venv/bin/python 'tree-sitter-languages'
