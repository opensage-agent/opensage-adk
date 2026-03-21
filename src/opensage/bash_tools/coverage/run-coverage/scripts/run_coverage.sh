#!/bin/bash
set -euo pipefail

# run_coverage.sh
# Usage:
#   TARGET_BINARY=/path/to/target run_coverage.sh <testcase_path>

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <testcase_path>" >&2
  exit 2
fi

TESTCASE_PATH=$1

if [[ ! "$TESTCASE_PATH" == /shared* ]]; then
  echo "Error: testcase_path must be in /shared"
  exit 1
fi

# Calculate MD5 to determine storage location.
# Use the testcase's canonical real path (not file contents) so the same path
# maps to the same coverage directory across content changes.
if command -v realpath >/dev/null 2>&1; then
  TESTCASE_REALPATH="$(realpath "$TESTCASE_PATH")"
else
  TESTCASE_REALPATH="$(readlink -f "$TESTCASE_PATH")"
fi

if [[ -z "${TESTCASE_REALPATH:-}" ]]; then
  echo "Error: failed to resolve testcase realpath: $TESTCASE_PATH" >&2
  exit 1
fi

MD5_HASH=$(printf '%s' "$TESTCASE_REALPATH" | md5sum | awk '{ print $1 }')
SUBDIR1=${MD5_HASH:0:2}
SUBDIR2=${MD5_HASH:2:2}
DST_DIR="/shared/.opensage/coverage/$SUBDIR1/$SUBDIR2/$MD5_HASH"
DST_PATH="$DST_DIR/testcase"

mkdir -p "$DST_DIR"
cp "$TESTCASE_PATH" "$DST_PATH"

if [[ -z "${TARGET_BINARY:-}" ]]; then
  echo "Error: TARGET_BINARY env var is required (full path recommended)." >&2
  exit 2
fi

BINARY_PATH="$TARGET_BINARY"

if [[ ! -x "$BINARY_PATH" ]]; then
  echo "Error: target binary not found or not executable: $BINARY_PATH" >&2
  exit 1
fi

has_llvm_coverage_instrumentation() {
  local binary_path="$1"

  if command -v llvm-readobj >/dev/null 2>&1; then
    llvm-readobj --sections "$binary_path" 2>/dev/null \
      | grep -Eq '__llvm_covmap|__llvm_prf'
    return $?
  fi

  if command -v readelf >/dev/null 2>&1; then
    readelf -S "$binary_path" 2>/dev/null | grep -Eq '__llvm_covmap|__llvm_prf'
    return $?
  fi

  return 2
}

if ! has_llvm_coverage_instrumentation "$BINARY_PATH"; then
  case $? in
    1)
      echo "Error: binary appears to be missing LLVM coverage instrumentation." >&2
      echo "  Expected to find __llvm_covmap/__llvm_prf sections in: $BINARY_PATH" >&2
      echo "  Rebuild with -fprofile-instr-generate -fcoverage-mapping." >&2
      exit 1
      ;;
    2)
      echo "Error: cannot verify coverage instrumentation (missing llvm-readobj/readelf)." >&2
      exit 1
      ;;
  esac
fi

PROFRAW="$DST_DIR/testcase.profraw"
PROFDATA="$DST_DIR/testcase.profdata"

# Run target binary to generate profile
LLVM_PROFILE_FILE="$PROFRAW" "$BINARY_PATH" "$DST_PATH" &> /dev/null

# Merge profile data
llvm-profdata merge -sparse -o "$PROFDATA" "$PROFRAW"

# Export to JSON
VERSION=$(llvm-cov --version | grep 'LLVM version' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
EXPORT_FLAG_SKIP_EXPANSIONS=""
EXPORT_FLAG_IGNORE=""
if dpkg --compare-versions "$VERSION" "ge" "8.0.0"; then
    EXPORT_FLAG_SKIP_EXPANSIONS="-skip-expansions"
fi

if dpkg --compare-versions "$VERSION" "ge" "7.0.0"; then
    EXPORT_FLAG_IGNORE="-ignore-filename-regex=.*src/libfuzzer/.*"
fi

llvm-cov export $EXPORT_FLAG_IGNORE $EXPORT_FLAG_SKIP_EXPANSIONS \
    -format=text \
    -instr-profile="$PROFDATA" \
    -object="$BINARY_PATH" > "$DST_DIR/testcase.json"

[ -f "$DST_DIR/testcase.json" ] || exit 1

# Generate text report
llvm-cov report \
    -instr-profile="$PROFDATA" \
    -object="$BINARY_PATH" > "$DST_DIR/report.txt"

if [ -f "$DST_DIR/report.txt" ]; then
    sed -n '1p;$p' "$DST_DIR/report.txt"
else
    echo "No report generated."
    exit 1
fi

# Best-effort upload to Neo4j.
# - Neo4jInitializer may write env vars to /shared/bashrc; source it if present.
# - Upload failures must NOT fail coverage generation (warn only).
if [ -f /shared/bashrc ]; then
  # shellcheck disable=SC1091
  source /shared/bashrc || true
fi

python3 /bash_tools/coverage/run-coverage/scripts/upload_coverage_to_neo4j.py \
  --testcase-id "$MD5_HASH" || echo "WARN: coverage upload script failed (ignored)" >&2
