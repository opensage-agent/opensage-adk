#!/bin/bash -eu

apt-get update

export PATH="/usr/bin:$PATH"

SCRIPT_DIR=$(dirname "$(readlink -f "$0")")

export SANITIZER=coverage
export FUZZING_ENGINE=${FUZZING_ENGINE:-libfuzzer}
export FUZZING_LANGUAGE=${FUZZING_LANGUAGE:-c++}
export ARCHITECTURE=${ARCHITECTURE:-x86_64}

# echo "[*] backup old files"
# mv $OUT $OUT.bak && mkdir $OUT
# mv $WORK $WORK.bak && mkdir $WORK
echo "[*] Clean up old builds"
rm -rf $OUT $WORK && mkdir -p $OUT $WORK


# fix sanitize-coverage
# Find env vars whose values contain "-fsanitize-coverage" and replace that flag
# with LLVM source-based coverage flags.
COVERAGE_REPLACEMENT_FLAGS='-fprofile-instr-generate -fcoverage-mapping -pthread -Wl,--no-as-needed -Wl,-ldl -Wl,-lm -Wno-unused-command-line-argument'

echo "[*] fixing -fsanitize-coverage flags in environment..."
while IFS= read -r line; do
  var="${line%%=*}"
  val="${line#*=}"

  new_val=$COVERAGE_REPLACEMENT_FLAGS

  if printf '%s\n' "$var" | grep -qi 'coverage'; then
    printf '[*]   %s updated\n' "$var"
    printf '      old: %s\n' "$val"
    printf '      new: %s\n' "$new_val"

    # Safely export updated value (handles spaces, etc.)
    printf -v q '%q' "$new_val"
    eval "export ${var}=${q}"
  fi
done < <(env | grep -F -- '-fsanitize-coverage' || true)

echo "[*] Fixing different project settings..."
source "$SCRIPT_DIR/fix_project"

compile
