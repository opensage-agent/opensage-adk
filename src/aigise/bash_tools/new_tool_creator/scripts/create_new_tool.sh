#!/bin/bash

# create_new_tool.sh - Create a new Agent Skill tool
# Usage: ./create_new_tool.sh <tool-name> [--category <category>]

set -e

TOOL_NAME=""
CATEGORY="general"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --category)
            CATEGORY="$2"
            shift 2
            ;;
        *)
            if [ -z "$TOOL_NAME" ]; then
                TOOL_NAME="$1"
            else
                echo '{"error": "Unexpected argument: '"$1"'", "success": false}' >&2
                exit 1
            fi
            shift
            ;;
    esac
done

# Validate tool name
if [ -z "$TOOL_NAME" ]; then
    echo '{"error": "Tool name is required", "success": false}' >&2
    exit 1
fi

# Validate tool name format (hyphen-case, lowercase, alphanumeric and hyphens only)
if ! echo "$TOOL_NAME" | grep -qE '^[a-z0-9-]+$'; then
    echo "{\"error\": \"Invalid tool name: '$TOOL_NAME'. Must be hyphen-case (lowercase letters, digits, hyphens only). Example: 'my-tool-name'\", \"success\": false, \"tool_name\": \"$TOOL_NAME\"}" >&2
    exit 1
fi

# Check length
if [ ${#TOOL_NAME} -gt 40 ]; then
    echo "{\"error\": \"Tool name too long: '$TOOL_NAME'. Maximum 40 characters.\", \"success\": false, \"tool_name\": \"$TOOL_NAME\"}" >&2
    exit 1
fi

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INIT_SCRIPT="$SCRIPT_DIR/../init_skill.py"

# Check if init_skill.py exists
if [ ! -f "$INIT_SCRIPT" ]; then
    echo "{\"error\": \"init_skill.py not found at $INIT_SCRIPT\", \"success\": false}" >&2
    exit 1
fi

# Construct the target path (bash_tools/new_tools/{category})
# Get the bash_tools directory (parent of new_tool_creator)
BASH_TOOLS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
TARGET_PATH="$BASH_TOOLS_DIR/new_tools/$CATEGORY"

# Run init_skill.py
python3 "$INIT_SCRIPT" "$TOOL_NAME" --path "$TARGET_PATH"

# Check exit code
if [ $? -eq 0 ]; then
    FULL_PATH="$TARGET_PATH/$TOOL_NAME"
    echo "{\"success\": true, \"tool_name\": \"$TOOL_NAME\", \"category\": \"$CATEGORY\", \"path\": \"$FULL_PATH\", \"message\": \"Tool created successfully\"}"
else
    echo "{\"success\": false, \"error\": \"Failed to create tool\", \"tool_name\": \"$TOOL_NAME\"}" >&2
    exit 1
fi
