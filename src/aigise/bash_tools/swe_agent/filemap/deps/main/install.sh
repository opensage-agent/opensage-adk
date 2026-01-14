#!/usr/bin/env bash
set -euo pipefail

# Ensure helper functions/env are available when present.
if [ -f /shared/bashrc ]; then
  # shellcheck disable=SC1091
  source /shared/bashrc
fi

python3 -m pip install 'tree-sitter==0.21.3'
python3 -m pip install 'tree-sitter-languages'
