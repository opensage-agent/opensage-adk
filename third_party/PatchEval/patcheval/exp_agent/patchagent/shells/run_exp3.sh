#!/bin/bash
set -euo pipefail

MODEL_NAME=${1:-}
if [ -z "$MODEL_NAME" ]; then
  echo "Usage: bash shells/run_exp3.sh <model_name> [limit] [max_workers] [allow_clone]"
  exit 1
fi

LIMIT=${2:-0}
MAX_WORKERS=${3:-2}
ALLOW_CLONE=${4:-false}
PREFIX="${MODEL_NAME}_exp3"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCHAGENT_ENV="${SCRIPT_DIR}/../../../../../PatchAgent/.env"
if [ -f "${PATCHAGENT_ENV}" ]; then
  set -a
  # shellcheck disable=SC1090
  source "${PATCHAGENT_ENV}"
  set +a
  echo "[info] Loaded env from ${PATCHAGENT_ENV}"
else
  echo "[warn] PatchAgent .env not found: ${PATCHAGENT_ENV}"
fi

python run_patchagent_baseline.py \
  --dataset_path ../sweagent/dataset_nolocation.jsonl \
  --output_dir "./outputs/${PREFIX}" \
  --model "${MODEL_NAME}" \
  --max_workers "${MAX_WORKERS}" \
  --limit "${LIMIT}" \
  --local_repo_path ../../exp_llm/projects \
  --allow_clone "${ALLOW_CLONE}" \
  --fast false \
  --rounds 2

echo "Patch generation finished: ./outputs/${PREFIX}"
