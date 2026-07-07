#!/bin/bash
set -euo pipefail

# Prepare repositories required by PatchEval CVE metadata.
# This reuses the official PatchEval helper under exp_llm/projects.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECTS_DIR="${SCRIPT_DIR}/../../../exp_llm/projects"

if [ ! -d "${PROJECTS_DIR}" ]; then
  echo "projects dir not found: ${PROJECTS_DIR}"
  exit 1
fi

cd "${PROJECTS_DIR}"
python clone.py
echo "Repository preparation finished under: ${PROJECTS_DIR}"
