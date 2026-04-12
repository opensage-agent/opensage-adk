#!/usr/bin/env bash
set -euo pipefail

REPO_URL="git+https://github.com/ziyue-pan/mmp.git"

if command -v mmp >/dev/null 2>&1; then
  exit 0
fi

if command -v uv >/dev/null 2>&1; then
  uv tool install --force "$REPO_URL"
  exit 0
fi

if command -v pip >/dev/null 2>&1; then
  pip install "$REPO_URL"
  exit 0
fi

cat >&2 <<'EOF'
warning: external MMP CLI is not installed in this sandbox.
The OpenSage MMP wrapper now delegates to an external implementation.
Neither `uv` nor `pip` is available to install `mmp`.
EOF
exit 1
