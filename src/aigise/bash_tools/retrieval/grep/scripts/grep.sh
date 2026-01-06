#!/bin/bash

# grep.sh - Search the codebase
# Usage: ./grep.sh "pattern"

if [ -z "$1" ]; then
    echo "Error: No pattern provided"
    exit 1
fi

PATTERN="$1"

# Run grep command and output results directly
# We use || true to ensure success exit code even if no matches found
grep -rniE "$PATTERN" -- /src 2>/dev/null | head -150 || true
