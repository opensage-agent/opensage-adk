#!/usr/bin/env bash
set -euo pipefail

if [ -f /shared/bashrc ]; then
  # shellcheck disable=SC1091
  source /shared/bashrc
fi

VENV_DIR="/app/.venv"
VENV_PY="$VENV_DIR/bin/python"
if [ ! -x "$VENV_PY" ]; then
  if [ ! -w /app ]; then
    VENV_DIR="/shared/app/.venv/swe_agent_web_browser"
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
  uv pip install --python "$VENV_PY" flask requests playwright
else
  "$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "$VENV_PY" -m pip install flask requests playwright
fi
"$VENV_PY" -m playwright install-deps chromium

if [ -f /usr/bin/google-chrome ]; then
  export WEB_BROWSER_CHROMIUM_EXECUTABLE_PATH=/usr/bin/google-chrome
elif [ -f /usr/bin/chromium ]; then
  export WEB_BROWSER_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium
elif [ -f /usr/bin/google-chrome-stable ]; then
  export WEB_BROWSER_CHROMIUM_EXECUTABLE_PATH=/usr/bin/google-chrome-stable
else
  "$VENV_PY" -m playwright install chromium
fi

export WEB_BROWSER_SCREENSHOT_MODE=print
export WEB_BROWSER_PORT=19321

mkdir -p /root/.web_browser_logs

run_web_browser_server &> /root/.web_browser_logs/web-browser-server.log &
