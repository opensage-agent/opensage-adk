#!/usr/bin/env bash
set -euo pipefail

# This install script persists environment setup for later tool invocations by
# appending (idempotently) to /shared/bashrc.

bundle_dir=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# In your tree:
#   bundle_dir/../lib
#   bundle_dir/../bin
# because install.sh is at deps/main/install.sh
root_dir="$(cd -- "${bundle_dir}/../.." &> /dev/null && pwd)"
lib_dir="${root_dir}/lib"
bin_dir="${root_dir}/scripts"

bashrc="/shared/bashrc"
mkdir -p "$(dirname "$bashrc")"
touch "$bashrc"

# Markers to make updates idempotent and easy to remove/replace.
begin_marker="# >>> registry bundle env >>>"
end_marker="# <<< registry bundle env <<<"

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
# Added by registry install.sh
# Ensure registry python modules are importable
if [ -d "$lib_dir" ]; then
  case ":\${PYTHONPATH-}:" in
    *:"$lib_dir":*) ;;
    *) export PYTHONPATH="$lib_dir\${PYTHONPATH:+:\$PYTHONPATH}" ;;
  esac
fi

# Ensure registry helper scripts are on PATH (e.g., _read_env, _write_env)
if [ -d "$bin_dir" ]; then
  case ":\${PATH}:" in
    *:"$bin_dir":*) ;;
    *) export PATH="$bin_dir:\$PATH" ;;
  esac
fi
$end_marker
EOF

# Optionally print a short confirmation (safe for non-interactive logs)
echo "Updated $bashrc with PYTHONPATH=$lib_dir and PATH+=${bin_dir}"
