#!/bin/bash
set -euo pipefail

# show_coverage.sh
# Usage: show_coverage.sh <testcase_path> <function_name> [file_path]

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <testcase_path> <function_name> [file_path]" >&2
  exit 2
fi

TESTCASE_PATH=$1
FUNCTION_NAME=$2
FILE_PATH=${3:-}

if [[ ! "$TESTCASE_PATH" == /shared* ]]; then
  echo "Error: testcase_path must be in /shared" >&2
  exit 1
fi

# Calculate testcase ID from testcase realpath (not file contents).
if command -v realpath >/dev/null 2>&1; then
  TESTCASE_REALPATH="$(realpath "$TESTCASE_PATH")"
else
  TESTCASE_REALPATH="$(readlink -f "$TESTCASE_PATH")"
fi

if [[ -z "${TESTCASE_REALPATH:-}" ]]; then
  echo "Error: failed to resolve testcase realpath: $TESTCASE_PATH" >&2
  exit 1
fi

TESTCASE_ID="$(printf '%s' "$TESTCASE_REALPATH" | md5sum | awk '{ print $1 }')"

# Calculate profdata path
SUBDIR1=${TESTCASE_ID:0:2}
SUBDIR2=${TESTCASE_ID:2:2}
PROFDATA_PATH="/shared/.opensage/coverage/$SUBDIR1/$SUBDIR2/$TESTCASE_ID/testcase.profdata"

if [[ -z "${TARGET_BINARY:-}" ]]; then
  echo "Error: TARGET_BINARY env var is required (full path recommended)." >&2
  exit 2
fi

BINARY_PATH="$TARGET_BINARY"

if [[ ! -x "$BINARY_PATH" ]]; then
  echo "Error: target binary not found or not executable: $BINARY_PATH" >&2
  exit 1
fi

if [ -n "$FILE_PATH" ]; then
    FILENAME=$(basename "$FILE_PATH")
    NAME_REGEX=".*${FILENAME}:${FUNCTION_NAME}"
else
    NAME_REGEX="$FUNCTION_NAME"
fi

# Determine version and run llvm-cov
VERSION=$(llvm-cov --version | grep 'LLVM version' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
SHOW_BRANCHES=""

if dpkg --compare-versions "$VERSION" "ge" "15.0.0"; then
    SHOW_BRANCHES="-show-branches=count"
fi

llvm-cov show \
    -object="$BINARY_PATH" \
    -instr-profile="$PROFDATA_PATH" \
    -show-line-counts-or-regions \
    $SHOW_BRANCHES \
    -name-regex="$NAME_REGEX"
