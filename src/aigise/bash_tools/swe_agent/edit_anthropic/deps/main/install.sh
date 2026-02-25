#!/usr/bin/env bash
set -euo pipefail

uv pip install --python /app/.venv/bin/python 'tree-sitter==0.21.3'
uv pip install --python /app/.venv/bin/python 'tree-sitter-languages'
