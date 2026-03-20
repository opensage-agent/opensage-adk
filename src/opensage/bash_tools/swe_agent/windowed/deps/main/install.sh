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
begin_marker="# >>> windowed bundle env >>>"
end_marker="# <<< windowed bundle env <<<"

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
cat >>"$bashrc" <<EOF
$begin_marker
# Added by windowed install.sh
if [ -d "$lib_dir" ]; then
  case ":\${PYTHONPATH-}:" in
    *:"$lib_dir":*) ;;
    *) export PYTHONPATH="$lib_dir\${PYTHONPATH:+:\$PYTHONPATH}" ;;
  esac
fi
$end_marker
EOF

# Write default environment variables into the environment storage
_write_env "WINDOW" "${WINDOW:-100}"
_write_env "OVERLAP" "${OVERLAP:-2}"
_write_env "FIRST_LINE" "${FIRST_LINE:-0}"
_write_env "CURRENT_FILE" "${CURRENT_FILE:-}"
