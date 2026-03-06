#!/bin/bash
# ==========================================================================
# AIgiSE → SLIME Training Launcher
#
# Usage (inside the slime container):
#   bash /root/aigise/rl/slime/train.sh                        # mock (default)
#   bash /root/aigise/rl/slime/train.sh --benchmark secodeplt  # secodeplt
#   bash /root/aigise/rl/slime/train.sh --debug --gpus 2,3     # debug mode
#   bash /root/aigise/rl/slime/train.sh --slime-config rl/slime/configs/secodeplt.yaml
# ==========================================================================

set -euo pipefail

BENCHMARK="mock_debug"
AGENT=""
DEBUG=0
GPUS="${CUDA_VISIBLE_DEVICES:-}"
SLIME_DIR="${SLIME_DIR:-/root/slime}"
AIGISE_DIR="${AIGISE_DIR:-/root/aigise}"
CLEAN_DOCKER=1
DATA_FILE=""
MAX_CONCURRENT="${AIGISE_MAX_CONCURRENT:-4}"
SLIME_CONFIG=""

declare -A BENCHMARK_AGENTS=(["mock_debug"]="mock_rl_agent" ["secodeplt"]="vul_agent_static_tools" ["cybergym"]="poc_agent_static_tools")
declare -A BENCHMARK_DATA=(["mock_debug"]="/root/aigise_data/mock_tasks.jsonl" ["secodeplt"]="/root/aigise_data/secodeplt_tasks.jsonl")

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --benchmark NAME      Benchmark name (default: mock_debug)
                        Available: mock_debug, secodeplt, cybergym
  --agent NAME          Agent name (default: auto-selected per benchmark)
  --gpus IDS            CUDA_VISIBLE_DEVICES (e.g. "2,3")
  --data FILE           Path to SLIME JSONL data file (default: auto-selected)
  --slime-dir DIR       Path to SLIME root (default: /root/slime)
  --aigise-dir DIR      Path to AIgiSE root (default: /root/aigise)
  --max-concurrent N    Max concurrent evaluations (default: 4)
  --slime-config FILE   YAML config for SLIME train.py parameters
                        (overrides defaults in the launch script)
  --debug               Use debug launch script (verbose logging)
  --no-clean            Skip Docker container cleanup
  -h, --help            Show this help

SLIME Config:
  Use --slime-config to pass a YAML file with SLIME train.py CLI args.
  Keys are arg names without leading --, values are arg values.
  Boolean keys with value 'true' become flags; 'false' are skipped.
  Example: rl/slime/configs/secodeplt.yaml
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --benchmark)    BENCHMARK="$2"; shift 2 ;;
        --agent)        AGENT="$2"; shift 2 ;;
        --gpus)         GPUS="$2"; shift 2 ;;
        --data)         DATA_FILE="$2"; shift 2 ;;
        --slime-dir)    SLIME_DIR="$2"; shift 2 ;;
        --aigise-dir)   AIGISE_DIR="$2"; shift 2 ;;
        --max-concurrent) MAX_CONCURRENT="$2"; shift 2 ;;
        --slime-config) SLIME_CONFIG="$2"; shift 2 ;;
        --debug)        DEBUG=1; shift ;;
        --no-clean)     CLEAN_DOCKER=0; shift ;;
        -h|--help)      usage ;;
        *)
            echo "Warning: unknown option '$1' (ignored)"
            shift
            ;;
    esac
done

[[ -z "$AGENT" ]] && AGENT="${BENCHMARK_AGENTS[$BENCHMARK]:-mock_rl_agent}"
[[ -z "$DATA_FILE" ]] && DATA_FILE="${BENCHMARK_DATA[$BENCHMARK]:-}"

# Parse SLIME config YAML → EXTRA_TRAIN_ARGS
if [[ -n "$SLIME_CONFIG" ]]; then
    # Resolve relative paths against AIGISE_DIR
    [[ "$SLIME_CONFIG" != /* ]] && SLIME_CONFIG="${AIGISE_DIR}/${SLIME_CONFIG}"
    [[ -f "$SLIME_CONFIG" ]] || { echo "ERROR: slime config not found: $SLIME_CONFIG"; exit 1; }

    EXTRA_TRAIN_ARGS=$(python3 -c "
import yaml, sys
cfg = yaml.safe_load(open(sys.argv[1]))
if not cfg:
    sys.exit(0)
args = []
for k, v in cfg.items():
    if isinstance(v, bool):
        if v:
            args.append(f'--{k}')
    else:
        args.append(f'--{k}')
        args.append(str(v))
print(' '.join(args))
" "$SLIME_CONFIG")
    export EXTRA_TRAIN_ARGS
    echo "SLIME config:  ${SLIME_CONFIG}"
    echo "  → EXTRA_TRAIN_ARGS=${EXTRA_TRAIN_ARGS}"
fi

AGENT_DIR="${AIGISE_DIR}/examples/agents/${AGENT}"
for d in "$SLIME_DIR" "$AIGISE_DIR" "$AGENT_DIR"; do
    [[ -d "$d" ]] || { echo "ERROR: directory not found: $d"; exit 1; }
done
[[ -n "$DATA_FILE" && ! -f "$DATA_FILE" ]] && { echo "ERROR: data file not found: $DATA_FILE"; exit 1; }

if [[ "$DEBUG" -eq 1 ]]; then
    LAUNCH_SCRIPT="${SLIME_DIR}/examples/aigise/run_qwen3_4B_debug.sh"
else
    LAUNCH_SCRIPT="${SLIME_DIR}/examples/aigise/run_qwen3_4B.sh"
fi
[[ -f "$LAUNCH_SCRIPT" ]] || { echo "ERROR: launch script not found: $LAUNCH_SCRIPT"; exit 1; }

if [[ "$CLEAN_DOCKER" -eq 1 ]]; then
    echo "Cleaning up aigise_* Docker containers..."
    CONTAINERS=$(docker ps -aq --filter 'name=aigise_' 2>/dev/null || true)
    if [[ -n "$CONTAINERS" ]]; then
        docker rm -f $CONTAINERS 2>/dev/null || true
        echo "  Removed $(echo "$CONTAINERS" | wc -w) container(s)"
    else
        echo "  No aigise_* containers found"
    fi
    docker volume prune -f 2>/dev/null | tail -1 || true
fi

MODE="production"; [[ "$DEBUG" -eq 1 ]] && MODE="debug"
echo ""
echo "============================================"
echo "  AIgiSE → SLIME Training (${MODE})"
echo "  Benchmark:  ${BENCHMARK} / ${AGENT}"
echo "  Data:       ${DATA_FILE:-<auto>}"
echo "  GPUs:       ${GPUS:-<all>}"
echo "  Script:     ${LAUNCH_SCRIPT}"
[[ -n "$SLIME_CONFIG" ]] && echo "  Config:     ${SLIME_CONFIG}"
echo "============================================"
echo ""
export AIGISE_AGENT_NAME="$AGENT"
export AIGISE_BENCHMARK_NAME="$BENCHMARK"
export AIGISE_MAX_CONCURRENT="$MAX_CONCURRENT"
export AIGISE_SRC="${AIGISE_DIR}/src"
[[ -n "$DATA_FILE" ]] && export AIGISE_DATA_FILE="$DATA_FILE"
[[ -n "$GPUS" ]] && export CUDA_VISIBLE_DEVICES="$GPUS"
exec bash "$LAUNCH_SCRIPT"
