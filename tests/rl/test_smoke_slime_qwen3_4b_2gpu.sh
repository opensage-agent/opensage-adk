#!/bin/bash
# ==========================================================================
# Smoke Test — Qwen3-4B, 2 GPU (TP=2)
#
# Run inside the SLIME container before merging / releasing.
# Uses mock_debug benchmark with --debug-rollout-only (no training step).
# Requires 2+ GPUs (tensor-parallel-size=2). NOT run by CI.
#
# Usage:
#   bash /root/aigise/tests/rl/test_smoke_qwen3_4b_2gpu.sh
#   bash /root/aigise/tests/rl/test_smoke_qwen3_4b_2gpu.sh --gpus 6,7
# ==========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
AIGISE_DIR="${SCRIPT_DIR}/.."
SLIME_DIR="${SLIME_DIR:-/root/slime}"
GPUS=""
TIMEOUT=300
RAY_ADDR="http://127.0.0.1:8265"
JOB_ID="aigise-debug"          # must match --submission-id in launch script
LOG_FILE="/tmp/aigise_smoke_test.log"
MIN_GPUS=2                     # Qwen3-4B requires TP=2

while [[ $# -gt 0 ]]; do
    case "$1" in
        --gpus)     GPUS="$2"; shift 2 ;;
        --timeout)  TIMEOUT="$2"; shift 2 ;;
        *)          echo "Unknown option: $1"; exit 1 ;;
    esac
done

pass() { echo -e "\033[32m✓ $1\033[0m"; }
fail() { echo -e "\033[31m✗ $1\033[0m"; FAILURES=$((FAILURES + 1)); }
FAILURES=0

echo ""
echo "============================================"
echo "  SLIME Integration Smoke Test"
echo "============================================"
echo ""

# --- Pre-flight checks ---
echo "--- Pre-flight checks ---"

[[ -d "$SLIME_DIR" ]] && pass "SLIME dir exists: $SLIME_DIR" || { fail "SLIME dir not found: $SLIME_DIR"; exit 1; }
[[ -d "$AIGISE_DIR/src/aigise" ]] && pass "AIgiSE dir exists: $AIGISE_DIR" || { fail "AIgiSE dir not found"; exit 1; }

python3 -c "import aigise" 2>/dev/null && pass "aigise importable" || { fail "aigise not importable (pip install -e ?)"; exit 1; }
python3 -c "import slime" 2>/dev/null && pass "slime importable" || { fail "slime not importable"; exit 1; }

command -v ray &>/dev/null && pass "ray CLI available" || { fail "ray not found"; exit 1; }
command -v nvidia-smi &>/dev/null && pass "nvidia-smi available" || { fail "nvidia-smi not found"; exit 1; }

DATA_FILE="${AIGISE_DIR}/src/aigise/evaluations/mock_debug/mock_test_dataset.json"
[[ -f "$DATA_FILE" ]] && pass "Mock dataset exists" || { fail "Mock dataset not found: $DATA_FILE"; exit 1; }

# Check SLIME launch script
LAUNCH_SCRIPT="${SLIME_DIR}/examples/aigise/run_qwen3_4B_debug.sh"
[[ -f "$LAUNCH_SCRIPT" ]] && pass "Debug launch script exists" || { fail "Launch script not found: $LAUNCH_SCRIPT"; exit 1; }

# Check model checkpoint
[[ -d "/root/Qwen3-4B-Instruct-2507" ]] && pass "Model checkpoint exists" || { fail "Model checkpoint not found at /root/Qwen3-4B-Instruct-2507"; exit 1; }

echo ""

# --- Generate SLIME JSONL if missing ---
SLIME_DATA="/root/aigise_data/mock_tasks.jsonl"
if [[ ! -f "$SLIME_DATA" ]]; then
    echo "--- Generating SLIME JSONL data ---"
    mkdir -p /root/aigise_data
    python3 "${SLIME_DIR}/examples/aigise/aigise_mock.py" \
        --local_dir /root/aigise_data \
        --dataset_path "$DATA_FILE" \
        --output_filename mock_tasks.jsonl
    [[ -f "$SLIME_DATA" ]] && pass "SLIME JSONL generated" || { fail "Failed to generate SLIME JSONL"; exit 1; }
else
    pass "SLIME JSONL data exists"
fi

echo ""

# --- Clean up ---
echo "--- Cleanup ---"
ray stop --force 2>/dev/null || true
sleep 1

# Kill any existing smoke test job
ray job stop --address="$RAY_ADDR" "$JOB_ID" 2>/dev/null || true

# Clean aigise containers
CONTAINERS=$(docker ps -aq --filter 'name=aigise_' 2>/dev/null || true)
if [[ -n "$CONTAINERS" ]]; then
    docker rm -f $CONTAINERS 2>/dev/null || true
    echo "  Cleaned up $(echo "$CONTAINERS" | wc -w) aigise container(s)"
fi

echo ""

# --- Detect GPU ---
if [[ -z "$GPUS" ]]; then
    GPUS=$(nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader,nounits | \
        awk -F', ' '{ if ($2 / $3 < 0.5) print $1 }' | head -n "$MIN_GPUS" | paste -sd,)
    GPU_COUNT=$(echo "$GPUS" | tr ',' '\n' | wc -l)
    if [[ -z "$GPUS" ]] || [[ "$GPU_COUNT" -lt "$MIN_GPUS" ]]; then
        fail "Need $MIN_GPUS available GPUs, found $GPU_COUNT"
        echo "  Use --gpus <ID,ID> to specify manually"
        exit 1
    fi
fi
GPU_COUNT=$(echo "$GPUS" | tr ',' '\n' | wc -l)
if [[ "$GPU_COUNT" -lt "$MIN_GPUS" ]]; then
    fail "Need $MIN_GPUS GPUs, got $GPU_COUNT (--gpus $GPUS)"
    exit 1
fi
pass "Using GPUs: $GPUS ($GPU_COUNT)"
export CUDA_VISIBLE_DEVICES="$GPUS"

echo ""

# --- Run rollout-only smoke test ---
echo "--- Running smoke test (rollout-only, mock_debug) ---"
echo "  Timeout: ${TIMEOUT}s"
echo "  Log: $LOG_FILE"
echo ""

export AIGISE_AGENT_NAME="mock_rl_agent"
export AIGISE_BENCHMARK_NAME="mock_debug"
export AIGISE_DATA_FILE="$SLIME_DATA"
export AIGISE_SRC="${AIGISE_DIR}/src"
export AIGISE_MAX_CONCURRENT=2
export NUM_GPUS="$MIN_GPUS"
export EXTRA_TRAIN_ARGS="--debug-rollout-only"

# Run the debug launch script with timeout, capture output
SMOKE_EXIT=0
timeout "$TIMEOUT" bash "$LAUNCH_SCRIPT" > "$LOG_FILE" 2>&1 || SMOKE_EXIT=$?

echo ""
echo "--- Results ---"

if [[ $SMOKE_EXIT -eq 124 ]]; then
    fail "Timed out after ${TIMEOUT}s"
elif [[ $SMOKE_EXIT -ne 0 ]]; then
    fail "Launch script exited with code $SMOKE_EXIT"
fi

# Check ray job status
JOB_STATUS=$(ray job status --address="$RAY_ADDR" "$JOB_ID" 2>/dev/null | grep -oP '(?<=status: )\w+' || echo "UNKNOWN")
if [[ "$JOB_STATUS" == "SUCCEEDED" ]]; then
    pass "Ray job status: SUCCEEDED"
else
    fail "Ray job status: $JOB_STATUS (expected SUCCEEDED)"
fi

# Check log for key success signals
if grep -q "reward" "$LOG_FILE" 2>/dev/null; then
    pass "Reward computation found in logs"
else
    fail "No reward computation in logs"
fi

if grep -q "Job 'aigise-" "$LOG_FILE" 2>/dev/null && grep -q "succeeded" "$LOG_FILE" 2>/dev/null; then
    pass "Job succeeded message in logs"
elif grep -q "SUCCEEDED" "$LOG_FILE" 2>/dev/null; then
    pass "SUCCEEDED found in logs"
else
    fail "No success signal in logs"
fi

if grep -qi "error\|traceback\|exception" "$LOG_FILE" 2>/dev/null; then
    # Filter out expected log lines that contain "error" in non-error context
    REAL_ERRORS=$(grep -i "error\|traceback\|exception" "$LOG_FILE" | \
        grep -v "AIGISE_LOG_LEVEL\|error_sample\|error_handling\|LOG_TO_STDERR\|ErrorHandling" | \
        head -5)
    if [[ -n "$REAL_ERRORS" ]]; then
        fail "Errors found in logs:"
        echo "$REAL_ERRORS" | sed 's/^/    /'
    else
        pass "No unexpected errors in logs"
    fi
else
    pass "No errors in logs"
fi

# --- Cleanup ---
ray stop --force 2>/dev/null || true

echo ""
echo "============================================"
if [[ $FAILURES -eq 0 ]]; then
    echo -e "  \033[32mSMOKE TEST PASSED\033[0m"
else
    echo -e "  \033[31mSMOKE TEST FAILED ($FAILURES failure(s))\033[0m"
    echo "  Full log: $LOG_FILE"
fi
echo "============================================"
echo ""

exit $FAILURES
