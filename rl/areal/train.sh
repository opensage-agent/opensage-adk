#!/bin/bash
# ==========================================================================
# OpenSage → AReaL Training Launcher
#
# Usage:
#   bash rl/areal/train.sh                              # 4-GPU default
#   bash rl/areal/train.sh --trial debug_v18             # custom trial name
#   bash rl/areal/train.sh --gpus 2,3 --ngpu 2           # 2-GPU mode
#   bash rl/areal/train.sh --areal-dir /path/to/AReaL    # custom AReaL path
# ==========================================================================

set -euo pipefail

AREAL_DIR="${AREAL_DIR:-}"
TRIAL=""
GPUS=""
NGPU=""
BATCH_SIZE=""
MAX_CONCURRENT=""
ALLOCATION=""
EXTRA_ARGS=()

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --trial NAME          Trial name (default: auto-generated timestamp)
  --gpus IDS            CUDA_VISIBLE_DEVICES (e.g. "2,3,5,6")
  --ngpu N              Number of GPUs (default: 4)
  --batch N             Training batch size (default: 2)
  --max-concurrent N    Max concurrent rollouts (default: 4)
  --allocation MODE     allocation_mode (default: sglang:d1p1t2+fsdp:d2p1t1)
  --areal-dir DIR       Path to AReaL root (default: auto-detect)
  -h, --help            Show this help

AReaL directory is resolved in order:
  1. --areal-dir argument
  2. AREAL_DIR environment variable
  3. ../AReaL (sibling directory to OpenSage)
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --trial)          TRIAL="$2"; shift 2 ;;
        --gpus)           GPUS="$2"; shift 2 ;;
        --ngpu)           NGPU="$2"; shift 2 ;;
        --batch)          BATCH_SIZE="$2"; shift 2 ;;
        --max-concurrent) MAX_CONCURRENT="$2"; shift 2 ;;
        --allocation)     ALLOCATION="$2"; shift 2 ;;
        --areal-dir)      AREAL_DIR="$2"; shift 2 ;;
        -h|--help)        usage ;;
        *)
            echo "Warning: unknown option '$1' (ignored)"
            shift
            ;;
    esac
done

# --- Resolve AReaL directory ---
if [[ -z "$AREAL_DIR" ]]; then
    # Try sibling directory
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    OPENSAGE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
    CANDIDATE="$(cd "$OPENSAGE_ROOT/.." && pwd)/AReaL"
    if [[ -d "$CANDIDATE/examples/opensage" ]]; then
        AREAL_DIR="$CANDIDATE"
    else
        echo "ERROR: Cannot find AReaL directory."
        echo "  Set AREAL_DIR or use --areal-dir, or place AReaL as a sibling to OpenSage."
        exit 1
    fi
fi

[[ -f "$AREAL_DIR/examples/opensage/run_opensage_grpo.sh" ]] || {
    echo "ERROR: $AREAL_DIR/examples/opensage/run_opensage_grpo.sh not found"
    exit 1
}

# --- Build args for AReaL script ---
ARGS=()
[[ -n "$TRIAL" ]]          && ARGS+=(--trial "$TRIAL")
[[ -n "$GPUS" ]]           && ARGS+=(--gpus "$GPUS")
[[ -n "$NGPU" ]]           && ARGS+=(--ngpu "$NGPU")
[[ -n "$BATCH_SIZE" ]]     && ARGS+=(--batch "$BATCH_SIZE")

# Pass env vars for options AReaL script reads from env
[[ -n "$GPUS" ]]           && export GPUS
[[ -n "$NGPU" ]]           && export NGPU
[[ -n "$MAX_CONCURRENT" ]] && export MAX_CONCURRENT
[[ -n "$ALLOCATION" ]]     && export ALLOCATION

echo ""
echo "============================================"
echo "  OpenSage → AReaL Training"
echo "  AReaL dir:  $AREAL_DIR"
[[ -n "$TRIAL" ]]      && echo "  Trial:      $TRIAL"
[[ -n "$GPUS" ]]        && echo "  GPUs:       $GPUS"
[[ -n "$ALLOCATION" ]]  && echo "  Allocation: $ALLOCATION"
echo "============================================"
echo ""

cd "$AREAL_DIR"
exec bash examples/opensage/run_opensage_grpo.sh "${ARGS[@]}"
