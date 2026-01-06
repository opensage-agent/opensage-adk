#!/bin/bash
set -euo pipefail

# check_fuzzing_stats.sh - Check fuzzing coverage and statistics
# Usage: check_fuzzing_stats.sh

# Check if fuzzing output directory exists
HAS_OUTPUT=false
if [ -d /fuzz/out ] 2>/dev/null; then
    HAS_OUTPUT=true
fi

echo "Fuzzing output directory: /fuzz/out/"
echo "Has output: $HAS_OUTPUT"

if [ "$HAS_OUTPUT" = true ]; then
    # Parse fuzzer_stats file
    STATS_FILE=$(find /fuzz/out -name 'fuzzer_stats' -type f 2>/dev/null | head -1)

    if [ -n "$STATS_FILE" ] && [ -f "$STATS_FILE" ]; then
        echo ""
        echo "Fuzzer statistics:"
        while IFS=':' read -r key value; do
            # Skip empty lines and comments
            if [[ -z "$key" ]] || [[ "$key" =~ ^# ]]; then
                continue
            fi

            key=$(echo "$key" | xargs)
            value=$(echo "$value" | xargs)

            # Skip if key or value is empty
            if [[ -z "$key" ]] || [[ -z "$value" ]]; then
                continue
            fi

            echo "  $key: $value"
        done < "$STATS_FILE"
    else
        echo "No fuzzer_stats file found"
    fi

    # Analyze fuzzing results (crashes, etc.)
    CRASHES_FOUND=0
    UNIQUE_CRASHES=0

    CRASHES_DIR=$(find /fuzz/out -name 'crashes' -type d 2>/dev/null | head -1)
    if [ -n "$CRASHES_DIR" ] && [ -d "$CRASHES_DIR" ]; then
        CRASH_LIST=$(ls -1 "$CRASHES_DIR" 2>/dev/null | grep -v -E '^(README\.txt|\.gitkeep)$' || true)
        if [ -n "$CRASH_LIST" ]; then
            CRASHES_FOUND=$(echo "$CRASH_LIST" | wc -l)
            UNIQUE_CRASHES=$CRASHES_FOUND
        fi
    fi

    echo ""
    echo "Results:"
    echo "  Crashes found: $CRASHES_FOUND"
    echo "  Unique crashes: $UNIQUE_CRASHES"
else
    echo "No fuzzing output directory found"
fi
