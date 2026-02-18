# Evaluations - Batch Processing Entry Point

Evaluation scripts run agents on benchmark datasets for performance measurement and testing.

## Command

```bash
cd src/opensage/evaluations
python cybergym/cybergym_vul_detection.py run \
  --agent-id my_agent \
  --config-path /path/to/config.toml \
  --max_llm_calls 75 \
  --use_multiprocessing \
  --max_workers 3
```

## Workflow details

For the full step-by-step workflow (dataset loading, parallel execution modes,
per-sample session/sandbox lifecycle, result collection, and evaluation), see
[Evaluations - workflow details](Evaluation-Workflow.md).
