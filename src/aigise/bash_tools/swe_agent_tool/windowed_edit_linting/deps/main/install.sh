#!/usr/bin/env bash
set -euo pipefail

if [ -f /shared/bashrc ]; then
  # shellcheck disable=SC1091
  source /shared/bashrc
fi

_write_env "CURRENT_FILE" "${CURRENT_FILE:-}"
_write_env "CURRENT_LINE" "${CURRENT_LINE:-0}"
_write_env "WINDOW" "${WINDOW:-}"

/app/.venv/bin/python -m pip install flake8
