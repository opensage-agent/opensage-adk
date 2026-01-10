#!/bin/bash

# create_new_tool.sh - Create a new Agent Skill tool
# Usage:
#   ./create_new_tool.sh <tool-name> --should_run_in_sandbox <sandbox> --returns_json <true|false> [--category <category>] [--requires_sandboxes "a,b,c"|"none"]

set -e

TOOL_NAME=""
CATEGORY="general"
SHOULD_RUN_IN_SANDBOX=""
RETURNS_JSON=""
REQUIRES_SANDBOXES="none"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --category)
            CATEGORY="$2"
            shift 2
            ;;
        --should_run_in_sandbox)
            SHOULD_RUN_IN_SANDBOX="$2"
            shift 2
            ;;
        --returns_json)
            RETURNS_JSON="$2"
            shift 2
            ;;
        --requires_sandboxes)
            REQUIRES_SANDBOXES="$2"
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

# Validate required execution sandbox
if [ -z "$SHOULD_RUN_IN_SANDBOX" ]; then
    echo '{"error": "--should_run_in_sandbox is required", "success": false}' >&2
    exit 1
fi

# Validate required returns_json flag
if [ -z "$RETURNS_JSON" ]; then
    echo '{"error": "--returns_json is required (true|false)", "success": false}' >&2
    exit 1
fi
RETURNS_JSON_LC="$(echo "$RETURNS_JSON" | tr '[:upper:]' '[:lower:]')"
if [[ "$RETURNS_JSON_LC" != "true" && "$RETURNS_JSON_LC" != "false" ]]; then
    echo "{\"error\": \"Invalid --returns_json value: '$RETURNS_JSON'. Must be true or false.\", \"success\": false}" >&2
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
python3 "$INIT_SCRIPT" "$TOOL_NAME" \
  --path "$TARGET_PATH" \
  --should_run_in_sandbox "$SHOULD_RUN_IN_SANDBOX" \
  --returns_json "$RETURNS_JSON_LC" \
  --requires_sandboxes "$REQUIRES_SANDBOXES"

# Check exit code
if [ $? -eq 0 ]; then
    FULL_PATH="$TARGET_PATH/$TOOL_NAME"
    echo "{\"success\": true, \"tool_name\": \"$TOOL_NAME\", \"category\": \"$CATEGORY\", \"path\": \"$FULL_PATH\", \"message\": \"Tool created successfully\"}"
else
    echo "{\"success\": false, \"error\": \"Failed to create tool\", \"tool_name\": \"$TOOL_NAME\"}" >&2
    exit 1
fi
