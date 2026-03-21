#!/bin/bash
set -euo pipefail

# show_coverage.sh
# Usage: show_coverage.sh <function_name> [file_path]
#
# Aggregates coverage across all executed testcases by merging all
# /shared/.opensage/coverage/**/testcase.profraw into:
#   /shared/.opensage/coverage/all/all.profdata
# and then runs llvm-cov show using the aggregated profile.

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <function_name> [file_path]" >&2
  exit 2
fi

FUNCTION_NAME=$1
FILE_PATH=${2:-}

if [[ -z "${TARGET_BINARY:-}" ]]; then
  echo "Error: TARGET_BINARY env var is required (full path recommended)." >&2
  exit 2
fi

BINARY_PATH="$TARGET_BINARY"
if [[ ! -x "$BINARY_PATH" ]]; then
  echo "Error: target binary not found or not executable: $BINARY_PATH" >&2
  exit 1
fi

if [[ -n "$FILE_PATH" ]]; then
  FILENAME=$(basename "$FILE_PATH")
  NAME_REGEX=".*${FILENAME}:${FUNCTION_NAME}"
else
  NAME_REGEX="$FUNCTION_NAME"
fi

ROOT_DIR="/shared/.opensage/coverage"
ALL_DIR="$ROOT_DIR/all"
ALL_PROFDATA_PATH="$ALL_DIR/all.profdata"

mkdir -p "$ALL_DIR"

PROFRAW_LIST_PATH="$ALL_DIR/profraw.list"
find "$ROOT_DIR" -type f -name "testcase.profraw" -print > "$PROFRAW_LIST_PATH"

PROFRAW_COUNT=$(grep -c . "$PROFRAW_LIST_PATH" || true)
if [[ "$PROFRAW_COUNT" -eq 0 ]]; then
  echo "Error: no testcase.profraw files found under: $ROOT_DIR" >&2
  echo "  Run the run-coverage skill first to generate coverage artifacts." >&2
  exit 1
fi

needs_rebuild=1
if [[ -f "$ALL_PROFDATA_PATH" ]]; then
  all_epoch="$(stat -c '%Y' "$ALL_PROFDATA_PATH" 2>/dev/null || echo 0)"
  newest_raw_epoch="$(
    find "$ROOT_DIR" -type f -name "testcase.profraw" -printf '%T@\n' \
      | sort -n | tail -1 | cut -d. -f1
  )"
  newest_raw_epoch="${newest_raw_epoch:-0}"
  if [[ "$all_epoch" -ge "$newest_raw_epoch" ]]; then
    needs_rebuild=0
  fi
fi

TMP_DIR=""
cleanup() {
  if [[ -n "${TMP_DIR:-}" && -d "$TMP_DIR" ]]; then
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

if [[ "$needs_rebuild" -eq 1 ]]; then
  echo "Aggregating coverage: merging $PROFRAW_COUNT profraw files..." >&2

  TMP_DIR="$(mktemp -d "$ALL_DIR/tmp_merge.XXXXXX")"

  current_list="$PROFRAW_LIST_PATH"
  pass=0

  while true; do
    current_count=$(grep -c . "$current_list" || true)

    # Keep argv bounded: never pass more than BATCH_SIZE inputs to a single
    # llvm-profdata invocation.
    BATCH_SIZE=100

    if [[ "$current_count" -le "$BATCH_SIZE" ]]; then
      mapfile -t inputs < "$current_list"
      llvm-profdata merge -sparse -o "$ALL_PROFDATA_PATH.new" "${inputs[@]}"
      mv -f "$ALL_PROFDATA_PATH.new" "$ALL_PROFDATA_PATH"
      break
    fi

    next_list="$TMP_DIR/pass_${pass}.list"
    : > "$next_list"

    batch_idx=0
    batch_list="$TMP_DIR/pass_${pass}_batch_${batch_idx}.list"
    : > "$batch_list"
    batch_lines=0

    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ -z "$line" ]] && continue
      printf '%s\n' "$line" >> "$batch_list"
      batch_lines=$((batch_lines + 1))

      if [[ "$batch_lines" -ge "$BATCH_SIZE" ]]; then
        batch_out="$TMP_DIR/pass_${pass}_batch_${batch_idx}.profdata"
        mapfile -t inputs < "$batch_list"
        llvm-profdata merge -sparse -o "$batch_out" "${inputs[@]}"
        printf '%s\n' "$batch_out" >> "$next_list"

        batch_idx=$((batch_idx + 1))
        batch_list="$TMP_DIR/pass_${pass}_batch_${batch_idx}.list"
        : > "$batch_list"
        batch_lines=0
      fi
    done < "$current_list"

    if [[ "$batch_lines" -gt 0 ]]; then
      batch_out="$TMP_DIR/pass_${pass}_batch_${batch_idx}.profdata"
      mapfile -t inputs < "$batch_list"
      llvm-profdata merge -sparse -o "$batch_out" "${inputs[@]}"
      printf '%s\n' "$batch_out" >> "$next_list"
    fi

    current_list="$next_list"
    pass=$((pass + 1))
  done
fi

if [[ ! -f "$ALL_PROFDATA_PATH" ]]; then
  echo "Error: aggregated profdata was not created: $ALL_PROFDATA_PATH" >&2
  exit 1
fi

# Determine version and run llvm-cov (keep output format consistent with the
# one-testcase coverage view).
VERSION=$(llvm-cov --version | grep 'LLVM version' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
SHOW_BRANCHES=""

if dpkg --compare-versions "$VERSION" "ge" "15.0.0"; then
  SHOW_BRANCHES="-show-branches=count"
fi

llvm-cov show \
  -object="$BINARY_PATH" \
  -instr-profile="$ALL_PROFDATA_PATH" \
  -show-line-counts-or-regions \
  $SHOW_BRANCHES \
  -name-regex="$NAME_REGEX"
