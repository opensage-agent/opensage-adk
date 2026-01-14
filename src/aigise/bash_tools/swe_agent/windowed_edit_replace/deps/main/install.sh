#!/usr/bin/env bash
set -euo pipefail

if [ -f /shared/bashrc ]; then
  # shellcheck disable=SC1091
  source /shared/bashrc
fi

_write_env "CURRENT_FILE" "${CURRENT_FILE:-}"
_write_env "CURRENT_LINE" "${CURRENT_LINE:-0}"
_write_env "WINDOW" "${WINDOW:-}"

VENV_DIR="/app/.venv"
VENV_PY="$VENV_DIR/bin/python"
if [ ! -x "$VENV_PY" ]; then
  if [ ! -w /app ]; then
    VENV_DIR="/shared/app/.venv/swe_agent_windowed_edit_replace"
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
  uv pip install --python "$VENV_PY" flake8
else
  "$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "$VENV_PY" -m pip install flake8
fi
