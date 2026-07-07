#!/bin/bash
set -euo pipefail

PREFIX=${1:-}
if [ -z "$PREFIX" ]; then
  echo "Usage: bash shells/run_eval.sh <prefix> [limit] [dataset_path]"
  exit 1
fi
LIMIT=${2:-0}
DATASET_PATH=${3:-}
if [ -z "$DATASET_PATH" ]; then
  if [[ "$PREFIX" == *_exp3 ]] || [[ "$PREFIX" == *_exp5 ]]; then
    DATASET_PATH="../sweagent/dataset_nolocation.jsonl"
  elif [[ "$PREFIX" == *_exp4 ]]; then
    DATASET_PATH="../sweagent/dataset_noknowledge.jsonl"
  else
    DATASET_PATH="../sweagent/dataset.jsonl"
  fi
fi

python evaluation/process_data.py \
  --output_dir "./outputs/${PREFIX}/patches" \
  --process_data_path "./evaluation/process_datas/${PREFIX}.jsonl" \
  --test_data_path ../../datasets/patcheval_dataset.json \
  --dataset_path "${DATASET_PATH}" \
  --limit "${LIMIT}"

python ../../evaluation/run_evaluation.py \
  --output "results/${PREFIX}" \
  --patch_file "./evaluation/process_datas/${PREFIX}.jsonl" \
  --input_file ../../datasets/input.json
