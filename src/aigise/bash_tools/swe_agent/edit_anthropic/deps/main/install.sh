#!/usr/bin/env bash
set -euo pipefail

# Install deps into the main sandbox uv venv explicitly.
# Ignore failures: https://github.com/SWE-agent/SWE-agent/issues/1179
VENV_DIR="/app/.venv"
VENV_PY="$VENV_DIR/bin/python"
if [ ! -x "$VENV_PY" ]; then
  if [ ! -w /app ]; then
    VENV_DIR="/shared/app/.venv/swe_agent_edit_anthropic"
  fi
  mkdir -p "$VENV_DIR"
  if command -v uv >/dev/null 2>&1; then
    uv venv --python python3 "$VENV_DIR"
  else
    python3 -m venv "$VENV_DIR"
  fi
  VENV_PY="$VENV_DIR/bin/python"
fi

if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$VENV_PY" 'tree-sitter==0.21.3' || true
  uv pip install --python "$VENV_PY" 'tree-sitter-languages' || true
else
  "$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "$VENV_PY" -m pip install 'tree-sitter==0.21.3' || true
  "$VENV_PY" -m pip install 'tree-sitter-languages' || true
fi
