#!/usr/bin/env bash
set -euo pipefail

# No extra deps required for this tool (kept for async_prepare_skill_deps hook).
if [ -f /shared/bashrc ]; then
  # shellcheck disable=SC1091
  source /shared/bashrc
fi
