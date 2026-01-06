#!/bin/bash

# joern_slice.sh - Get program slice using Joern
# Usage: ./joern_slice.sh "function_name" --file-path "path/to/file"

if [ -z "$1" ]; then
    echo "Error: No function name provided" >&2
    exit 1
fi

FUNCTION_NAME="$1"
FILE_PATH=""

# Simple argument parsing
# $2 might be --file-path, $3 is the path
if [ "$2" == "--file-path" ] && [ -n "$3" ]; then
    FILE_PATH="$3"
fi

# Check if joern-slice command exists
if ! command -v joern-slice >/dev/null 2>&1; then
    echo "Error: joern-slice command not found. Please ensure you are running in the joern sandbox." >&2
    exit 1
fi

# Check if /cpg.bin exists
if [ ! -f /cpg.bin ]; then
    echo "Error: /cpg.bin file not found. CPG must be generated first." >&2
    exit 1
fi

# joern-slice command
# We construct the command array conceptually
# joern-slice data-flow -o /tmp/slices.json --method-name-filter NAME [--file-filter FILE] /cpg.bin

CMD="joern-slice data-flow -o /tmp/slices.json --method-name-filter $FUNCTION_NAME"
if [ -n "$FILE_PATH" ]; then
    CMD="$CMD --file-filter $FILE_PATH"
fi
CMD="$CMD /cpg.bin"

# Run command, capture stderr for error reporting
# Store stderr in a temp file so we can show it if command fails
TMP_ERR=$(mktemp)
$CMD >/dev/null 2>"$TMP_ERR"
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "Error: joern-slice command failed" >&2
    if [ -s "$TMP_ERR" ]; then
        echo "Command: $CMD" >&2
        echo "Error output:" >&2
        cat "$TMP_ERR" >&2
    fi
    rm -f "$TMP_ERR"
    exit 1
fi
rm -f "$TMP_ERR"

if [ ! -f /tmp/slices.json ]; then
    echo "Error: joern-slice did not produce output" >&2
    exit 1
fi

# Use python inside bash to process the JSON output cleanly
python3 -c '
import json
import sys

try:
    with open("/tmp/slices.json", "r") as f:
        res = json.load(f)

    nodes = res.get("nodes", [])
    lines = {}
    for node in nodes:
        fp = node.get("parentFile")
        if not fp: continue
        if fp not in lines:
            lines[fp] = set()
        if "lineNumber" in node:
            lines[fp].add(node["lineNumber"])

    # Output as plain text
    if not lines:
        print("No slices found.")
    else:
        print("Found slices in {} file(s):\n".format(len(lines)))
        for fp, line_set in sorted(lines.items()):
            sorted_lines = sorted(list(line_set))
            print("File: {}".format(fp))
            print("  Lines: {}".format(", ".join(map(str, sorted_lines))))
            print()

except Exception as e:
    print("Error: Failed to process output: {}".format(str(e)), file=sys.stderr)
    sys.exit(1)
'
