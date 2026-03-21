#!/bin/bash
set -euo pipefail

# extract_crashes.sh - Extract crash inputs from fuzzing output
# Usage: extract_crashes.sh <target_dir> [crash_name1] [crash_name2] ...

if [ $# -lt 1 ]; then
    echo "Error: Missing target_dir argument" >&2
    exit 2
fi

TARGET_DIR="$1"
shift

# Find crashes directory
CRASHES_DIR=$(find /fuzz/out -name 'crashes' -type d 2>/dev/null | head -1)

if [ -z "$CRASHES_DIR" ] || [ ! -d "$CRASHES_DIR" ]; then
    echo "Error: No crashes directory found" >&2
    exit 1
fi

# Verify crashes directory is accessible
if ! ls -la "$CRASHES_DIR" >/dev/null 2>&1; then
    echo "Error: No crashes directory found" >&2
    exit 1
fi

# Create target directory
mkdir -p "$TARGET_DIR"

# Extract crashes
if [ $# -eq 0 ]; then
    # Extract all crashes
    cp -r "${CRASHES_DIR}/." "$TARGET_DIR"
else
    # Extract specific crashes
    for crash_name in "$@"; do
        if [ -f "${CRASHES_DIR}/${crash_name}" ]; then
            cp "${CRASHES_DIR}/${crash_name}" "$TARGET_DIR"
        else
            echo "Warning: Crash file not found: ${crash_name}" >&2
        fi
    done
fi

echo "Crashes extracted successfully to: $TARGET_DIR"
