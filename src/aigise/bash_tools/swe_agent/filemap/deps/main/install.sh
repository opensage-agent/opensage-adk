#!/usr/bin/env bash
set -euo pipefail

# Ensure helper functions/env are available when present.
if [ -f /shared/bashrc ]; then
  # shellcheck disable=SC1091
  source /shared/bashrc
fi

# Install deps into a venv (avoid PEP668 "externally-managed-environment").
# Preference order:
# 1) Use existing /app/.venv
# 2) If missing, try creating /app/.venv
# 3) If /app is not writable, fall back to /shared/app/.venv/<skill>
VENV_DIR="/app/.venv"
VENV_PY="$VENV_DIR/bin/python"
if [ ! -x "$VENV_PY" ]; then
  if [ ! -w /app ]; then
    VENV_DIR="/shared/app/.venv/swe_agent_filemap"
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
  uv pip install --python "$VENV_PY" 'tree-sitter==0.21.3'
  uv pip install --python "$VENV_PY" 'tree-sitter-languages'
else
  # Fallback for environments without uv.
  "$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "$VENV_PY" -m pip install 'tree-sitter==0.21.3'
  "$VENV_PY" -m pip install 'tree-sitter-languages'
fi
