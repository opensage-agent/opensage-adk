#!/bin/bash
set -euo pipefail

# simplified_python_fuzzer.sh - Run a simplified Python fuzzer script
# Usage: simplified_python_fuzzer.sh <script_content> [duration_seconds]
# The script content will be written to /tmp/fuzzer.py and executed
#
# NOTE: This tool only runs the provided script. The fuzzer script should be
# designed based on an analysis of the target harness and input format, and
# should prefer format-aware (grammar/structure-aware) mutations over blind
# byte-level mutations.

if [ $# -lt 1 ]; then
    echo "Usage: simplified_python_fuzzer.sh <script_content> [duration_seconds]" >&2
    exit 2
fi

SCRIPT_CONTENT="$1"
DURATION_SECONDS="${2:-180}"

# Write script to file
FUZZER_SCRIPT_PATH="/tmp/fuzzer.py"
echo "$SCRIPT_CONTENT" > "$FUZZER_SCRIPT_PATH"

# Run the fuzzer script with timeout and capture output
OUTPUT=""
EXIT_CODE=0
set +e  # Allow non-zero exit codes
OUTPUT=$(timeout "${DURATION_SECONDS}s" python3 "$FUZZER_SCRIPT_PATH" 2>&1)
EXIT_CODE=$?
set -e

# Check for crash files
CRASH_FILES=$(find /tmp -name 'crash_*' -o -name 'crash.txt' 2>/dev/null || true)

# Output results as text
echo "Fuzzer execution completed"
echo "Exit code: $EXIT_CODE"

if [ -n "$OUTPUT" ]; then
    echo ""
    echo "Output:"
    echo "$OUTPUT"
fi

if [ -n "$CRASH_FILES" ]; then
    CRASH_FILE_LIST=$(echo "$CRASH_FILES" | tr '\n' ' ' | sed 's/ $//')
    echo ""
    echo "Crash files found: $CRASH_FILE_LIST"
    echo ""
    echo "Crash details:"

    # Read first 5 crash files
    CRASH_COUNT=0
    while IFS= read -r crash_file && [ $CRASH_COUNT -lt 5 ]; do
        if [ -n "$crash_file" ] && [ -f "$crash_file" ]; then
            echo "--- $crash_file ---"
            head -n 100 "$crash_file" 2>/dev/null || echo "(empty or unreadable)"
            echo ""
            CRASH_COUNT=$((CRASH_COUNT + 1))
        fi
    done <<< "$CRASH_FILES"
else
    echo ""
    echo "No crash files found"
fi
