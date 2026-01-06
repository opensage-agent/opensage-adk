#!/bin/bash
set -euo pipefail

# run_coverage.sh
# Usage: run_coverage.sh <testcase_path>

TESTCASE_PATH=$1

if [[ ! "$TESTCASE_PATH" == /shared* ]]; then
  echo "Error: testcase_path must be in /shared"
  exit 1
fi

# Calculate MD5 to determine storage location
MD5_HASH=$(md5sum "$TESTCASE_PATH" | awk '{ print $1 }')
SUBDIR1=${MD5_HASH:0:2}
SUBDIR2=${MD5_HASH:2:2}
DST_DIR="/shared/.aigise/coverage/$SUBDIR1/$SUBDIR2/$MD5_HASH"
DST_PATH="$DST_DIR/testcase"

mkdir -p "$DST_DIR"
cp "$TESTCASE_PATH" "$DST_PATH"

TARGET_BINARY=${TARGET_BINARY:-target}
BINARY_PATH="/out/$TARGET_BINARY"

PROFRAW="$DST_DIR/testcase.profraw"
PROFDATA="$DST_DIR/testcase.profdata"

# Run target binary to generate profile
LLVM_PROFILE_FILE="$PROFRAW" "$BINARY_PATH" "$DST_PATH" &> /dev/null

# Merge profile data
llvm-profdata merge -sparse -o "$PROFDATA" "$PROFRAW"

# Export to JSON
llvm-cov export \
    -ignore-filename-regex=.*src/libfuzzer/.* \
    -format=text \
    -skip-expansions \
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
