#!/bin/bash -eu

BINARY=$1
INPUT=$2
OUT_DIR=$3

INPUT_NAME=$(basename "$INPUT")
PROFRAW="$OUT_DIR/$INPUT_NAME.profraw"
PROFDATA="$OUT_DIR/$INPUT_NAME.profdata"


if [ ! -d "$OUT_DIR" ]; then
  mkdir -p "$OUT_DIR"
fi

LLVM_PROFILE_FILE="$PROFRAW" "$BINARY" "$INPUT" &> /dev/null

llvm-profdata merge -sparse -o "$PROFDATA" "$PROFRAW"

llvm-cov export \
    -ignore-filename-regex=.*src/libfuzzer/.* \
    -format=text \
    -skip-expansions \
    -instr-profile="$PROFDATA" \
    -object="$BINARY" > "$OUT_DIR/$INPUT_NAME.json"

[ -f "$OUT_DIR/$INPUT_NAME.json" ] || exit 1

llvm-cov report \
    -instr-profile="$PROFDATA" \
    -object="$BINARY" > "$OUT_DIR/report.txt"
