#!/bin/bash
set -euo pipefail

# show_coverage.sh
# Usage: show_coverage.sh <testcase_id> <function_name> [file_path]

TESTCASE_ID=$1
FUNCTION_NAME=$2
FILE_PATH=${3:-}

# Calculate profdata path
SUBDIR1=${TESTCASE_ID:0:2}
SUBDIR2=${TESTCASE_ID:2:2}
PROFDATA_PATH="/shared/.aigise/coverage/$SUBDIR1/$SUBDIR2/$TESTCASE_ID/testcase.profdata"

TARGET_BINARY=${TARGET_BINARY:-target}
BINARY_PATH="/out/$TARGET_BINARY"

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
