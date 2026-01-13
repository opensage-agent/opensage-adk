#!/usr/bin/env bash
set -euo pipefail

if [ -f /shared/bashrc ]; then
  # shellcheck disable=SC1091
  source /shared/bashrc
fi

_write_env SEARCH_RESULTS "()"
_write_env SEARCH_FILES "()"
_write_env SEARCH_INDEX 0
