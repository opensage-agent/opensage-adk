#!/usr/bin/env bash
set -euo pipefail

if [ -f /shared/bashrc ]; then
  # shellcheck disable=SC1091
  source /shared/bashrc
fi

bundle_dir=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
root_dir="$(cd -- "${bundle_dir}/../.." &> /dev/null && pwd)"
lib_dir="${root_dir}/lib"

bashrc="/shared/bashrc"
mkdir -p "$(dirname "$bashrc")"
touch "$bashrc"

# Markers to make updates idempotent and easy to remove/replace.
begin_marker="# >>> web_browser bundle env >>>"
end_marker="# <<< web_browser bundle env <<<"

# Remove any previous block we added.
# Works even if markers are missing.
tmp="$(mktemp)"
awk -v b="$begin_marker" -v e="$end_marker" '
  $0==b {inblock=1; next}
  $0==e {inblock=0; next}
  !inblock {print}
' "$bashrc" > "$tmp"
cat "$tmp" > "$bashrc"
rm -f "$tmp"

# Append fresh block.

uv pip install --python /app/.venv/bin/python flask requests playwright
/app/.venv/bin/python -m playwright install-deps chromium

# Determine chromium path
CHROMIUM_PATH=""
if [ -f /usr/bin/google-chrome ]; then
  CHROMIUM_PATH=/usr/bin/google-chrome
elif [ -f /usr/bin/chromium ]; then
  CHROMIUM_PATH=/usr/bin/chromium
elif [ -f /usr/bin/google-chrome-stable ]; then
  CHROMIUM_PATH=/usr/bin/google-chrome-stable
else
  /app/.venv/bin/python -m playwright install chromium
fi


# Define scripts dir early for use in bashrc block
scripts_dir="${root_dir}/scripts"

# Append fresh block with ALL env vars
cat >>"$bashrc" <<EOF
$begin_marker
# Added by web_browser install.sh
if [ -d "$lib_dir" ]; then
  case ":\${PYTHONPATH-}:" in
    *:"$lib_dir":*) ;;
    *) export PYTHONPATH="$lib_dir\${PYTHONPATH:+:\$PYTHONPATH}" ;;
  esac
fi

# Add scripts dir to PATH
if [ -d "$scripts_dir" ]; then
  case ":\${PATH}:" in
    *:"$scripts_dir":*) ;;
    *) export PATH="$scripts_dir:\$PATH" ;;
  esac
fi

# Persist Web Browser Env Vars
if [ -n "$CHROMIUM_PATH" ]; then
  export WEB_BROWSER_CHROMIUM_EXECUTABLE_PATH="$CHROMIUM_PATH"
fi
export WEB_BROWSER_SCREENSHOT_MODE=print
export WEB_BROWSER_PORT=19321
$end_marker
EOF

# Source bashrc to apply changes to current shell
source "$bashrc"

mkdir -p /root/.web_browser_logs

# Create a wrapper for run_web_browser_server so it can be called as a command
# and uses the main sandbox venv python
cat > "$scripts_dir/run_web_browser_server" <<EOF
#!/usr/bin/env bash
exec /app/.venv/bin/python "$scripts_dir/run_web_browser_server.py" "\$@"
EOF
chmod +x "$scripts_dir/run_web_browser_server"
chmod +x "$scripts_dir/run_web_browser_server.py"

# Run the server
nohup run_web_browser_server > /root/.web_browser_logs/web-browser-server.log 2>&1 &
