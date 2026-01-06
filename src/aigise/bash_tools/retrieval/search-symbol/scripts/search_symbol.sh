#!/bin/bash

# search_symbol.sh - Search for symbol definition using ctags
# Usage: ./search_symbol.sh "symbol_name"

if [ -z "$1" ]; then
    echo "Error: No symbol name provided"
    exit 1
fi

SYMBOL_NAME="$1"
SRC_DIR="/src"
TAGS_FILE="/shared/.tags"
CTAGS_CMD="ctags --excmd=number --exclude=Makefile -f $TAGS_FILE -R $SRC_DIR"

# Ensure ctags is installed
if ! command -v ctags &> /dev/null; then
    apt-get update >/dev/null 2>&1 && apt-get install -y ctags >/dev/null 2>&1
    if [ $? -ne 0 ]; then
         echo "Error: Failed to install ctags, do not call this tool again."
         exit 1
    fi
fi

# Run ctags
$CTAGS_CMD >/dev/null 2>&1
if [ $? -ne 0 ]; then
     echo "Error: Failed to run ctags command, do not call this tool again."
     exit 1
fi

RESULT_MSG=""

# Handle :: in symbol name
if [[ "$SYMBOL_NAME" == *"::"* ]]; then
    # Extract last part
    SYMBOL_NAME="${SYMBOL_NAME##*::}"
    RESULT_MSG="Detected \`::\` in symbol_name. If you are looking for the definition of a method in a class, do not include the class name in the symbol_name. E.g. if the symbol name is 'MyClass::myMethod', do not include 'MyClass' in the symbol_name, only include 'myMethod'. Searching for '$SYMBOL_NAME':"
    echo "$RESULT_MSG"
fi

# Try exact match
EXACT_MATCH=$(grep -i "^$SYMBOL_NAME	" "$TAGS_FILE" 2>/dev/null)

if [ -n "$EXACT_MATCH" ]; then
    echo "$EXACT_MATCH"
    exit 0
fi

# Try fuzzy match
FUZZY_MATCH=$(grep -i "$SYMBOL_NAME" "$TAGS_FILE" 2>/dev/null)

if [ -z "$FUZZY_MATCH" ]; then
    echo "No matches found."
    exit 0
fi

# Return fuzzy matches with warning
echo "Note: No exact match found for '$SYMBOL_NAME'. Showing fuzzy matches:"
echo "$FUZZY_MATCH"
