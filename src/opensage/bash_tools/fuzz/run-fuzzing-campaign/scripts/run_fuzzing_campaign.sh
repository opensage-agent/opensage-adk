#!/bin/bash
set -euo pipefail

# run_fuzzing_campaign.sh - Run a complete AFL++ fuzzing campaign
# Usage: run_fuzzing_campaign.sh <fuzz_target> <duration_seconds> [seed_path1] [seed_path2] ... [--custom_mutator_path <path>] [--reset_output]

if [ $# -lt 2 ]; then
    echo "Usage: run_fuzzing_campaign.sh <fuzz_target> <duration_seconds> [seed_path1] [seed_path2] ... [--custom_mutator_path <path>] [--reset_output]" >&2
    exit 2
fi

FUZZ_TARGET="$1"
DURATION_SECONDS="$2"
shift 2

SEED_PATHS=()
CUSTOM_MUTATOR_PATH=""
RESET_OUTPUT=false

# Parse arguments: remaining positional args are seed paths, then optional flags
while [[ $# -gt 0 ]]; do
    case $1 in
        --custom_mutator_path)
            CUSTOM_MUTATOR_PATH="$2"
            shift 2
            ;;
        --reset_output)
            RESET_OUTPUT=true
            shift
            ;;
        --*)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
        *)
            # Positional argument - treat as seed path
            SEED_PATHS+=("$1")
            shift
            ;;
    esac
done

# Set up fuzzing directory
# Check if /fuzz/out exists and has fuzzer_stats
EXISTS_OUTPUT=false
if [ -f /fuzz/out/fuzzer_stats ] 2>/dev/null; then
    EXISTS_OUTPUT=true
fi

# Reset output directory if requested or if it doesn't exist
if [ "$RESET_OUTPUT" = true ] || [ "$EXISTS_OUTPUT" = false ]; then
    rm -rf /fuzz/out
    mkdir -p /fuzz/out
    EXISTS_OUTPUT=false
fi

# Clean and create input directory
rm -rf /fuzz/in
mkdir -p /fuzz/in

# Create mutator directory (mutator file should already be copied by Python if needed)
mkdir -p /fuzz/mutator

# Create seed inputs
if [ ${#SEED_PATHS[@]} -gt 0 ]; then
    for seed_path in "${SEED_PATHS[@]}"; do
        if [ ! -e "$seed_path" ]; then
            echo "Warning: Seed path does not exist: $seed_path" >&2
            continue
        fi
        cp -r "$seed_path" /fuzz/in/
    done
elif [ "$EXISTS_OUTPUT" = false ]; then
    # Create a default seed input if no previous state
    echo "1234" > /fuzz/in/seed.txt
fi

# Set up environment variables
export AFL_NO_UI=1
if [ -n "$CUSTOM_MUTATOR_PATH" ]; then
    export PYTHONPATH=/fuzz/mutator
    export AFL_PYTHON_MODULE=custom_mutator
fi

# Run AFL++ fuzzer
set +e  # Temporarily allow errors
timeout "${DURATION_SECONDS}s" /out/afl-fuzz -i /fuzz/in -o /fuzz/out "/out/${FUZZ_TARGET}"
FUZZER_EXIT_CODE=$?
set -e  # Re-enable error checking

# Analyze results and output as JSON
CRASHES_FOUND=0
UNIQUE_CRASHES=0
EXECUTIONS=0
EXEC_SPEED=0
SUCCESS=false
ERROR_MSG=""
FAILURE_HINT="Please check if /out/afl-fuzz exists, if not, the binary is compiled with libfuzzer, please use libfuzzer instead"

# Get crash statistics
CRASHES_DIR=$(find /fuzz/out -name 'crashes' -type d 2>/dev/null | head -1)
if [ -n "$CRASHES_DIR" ] && [ -d "$CRASHES_DIR" ]; then
    CRASH_LIST=$(ls -1 "$CRASHES_DIR" 2>/dev/null | grep -v -E '^(README\.txt|\.gitkeep)$' || true)
    if [ -n "$CRASH_LIST" ]; then
        CRASHES_FOUND=$(echo "$CRASH_LIST" | wc -l)
        UNIQUE_CRASHES=$CRASHES_FOUND
    fi
fi

# Get execution statistics from fuzzer_stats
STATS_FILE=$(find /fuzz/out -name 'fuzzer_stats' -type f 2>/dev/null | head -1)
if [ -n "$STATS_FILE" ] && [ -f "$STATS_FILE" ]; then
    # If fuzzer_stats exists, fuzzing ran successfully
    SUCCESS=true

    # Extract execs_done
    EXECS_DONE=$(grep "^execs_done" "$STATS_FILE" 2>/dev/null | cut -d':' -f2 | tr -d ' ' || echo "0")
    if [ -n "$EXECS_DONE" ] && [ "$EXECS_DONE" != "" ]; then
        EXECUTIONS=$EXECS_DONE
    fi

    # Extract execs_per_sec
    EXECS_PER_SEC=$(grep "^execs_per_sec" "$STATS_FILE" 2>/dev/null | cut -d':' -f2 | tr -d ' ' || echo "0")
    if [ -n "$EXECS_PER_SEC" ] && [ "$EXECS_PER_SEC" != "" ]; then
        EXEC_SPEED=$EXECS_PER_SEC
    fi
fi

# Check if target binary exists
if [ ! -f "/out/${FUZZ_TARGET}" ]; then
    SUCCESS=false
    ERROR_MSG="Fuzz target binary not found: /out/${FUZZ_TARGET}"
fi

# Output results as text
if [ "$SUCCESS" = true ]; then
    echo "Fuzzing campaign completed successfully"
else
    if [ -n "${ERROR_MSG:-}" ]; then
        echo "Error: $ERROR_MSG" >&2
        echo "$FAILURE_HINT" >&2
        exit 1
    fi
    echo "Fuzzing campaign completed with warnings"
fi

echo "Fuzz target: $FUZZ_TARGET"
echo "Duration: $((DURATION_SECONDS / 60)) minutes"
echo "Results:"
echo "  Crashes found: $CRASHES_FOUND"
echo "  Unique crashes: $UNIQUE_CRASHES"
echo "  Executions: $EXECUTIONS"
echo "  Execution speed: $EXEC_SPEED exec/s"
