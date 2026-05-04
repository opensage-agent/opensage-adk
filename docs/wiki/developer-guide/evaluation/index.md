---
icon: lucide/scale
---

# Evaluations

Evaluations run agents on benchmark datasets for performance measurement and testing. The evaluation system is built on the `Evaluation` base class, which handles parallel execution, sandbox lifecycle, result collection, and metrics.

## Running an Evaluation

Evaluations use Python Fire for their CLI. The general form:

```bash
python -m opensage.evaluations.<benchmark>.<module> <method> [options]
```

**Example:**

```bash
python -m opensage.evaluations.cybergym.cybergym_vul_detection run \
  --dataset_path="org/dataset" \
  --agent_dir="examples/agents/my_agent" \
  --max_workers=6 \
  --use_multiprocessing=true
```

## Execution Modes

| Method | Mode | Best for |
|--------|------|----------|
| `run` | Auto-select (multiprocessing or threading based on `use_multiprocessing`), then `evaluate()` | **Production runs** |
| `run_debug` | Single-threaded, then `evaluate()` | **Debugging** |
| `generate` | Multiprocessing only (no evaluation step) | Explicit parallelism |
| `generate_threaded` | Threading only (no evaluation step) | When multiprocessing has serialization issues |
| `generate_single_thread` | Sequential (no evaluation step) | Step-by-step debugging |

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `dataset_path` | str | Required | HuggingFace dataset or local path |
| `agent_dir` | str | Required | Directory containing `agent.py` |
| `max_llm_calls` | int | 100 | Maximum LLM calls per task |
| `max_workers` | int | 6 | Parallel workers |
| `use_multiprocessing` | bool | True | Use multiprocessing vs threading |
| `use_sandbox_cache` | bool | True | Cache/restore sandbox states |
| `run_until_explicit_finish` | bool | True | Keep running until agent signals done |
| `use_config_model` | bool | False | Use model from config file |
| `llm_retry_count` | int | 3 | Retries for LLM API calls |
| `llm_retry_timeout` | int | 30 | Timeout per LLM request (seconds) |
| `log_level` | str | "INFO" | Terminal log level |

## Output Structure

Each run creates a timestamped output directory:

```
evals/
└── myevaluation/
    └── yymmdd_HHMMSS/
        ├── evaluation_master.log       # Master log
        ├── eval_params.json            # Parameters used
        ├── task_001/
        │   ├── execution_debug.log     # DEBUG-level log
        │   ├── execution_info.log      # INFO-level log
        │   ├── config_used.toml        # Config for this task
        │   ├── cost_info.json          # Token usage and costs
        │   ├── session_trace.json      # Complete session events
        │   ├── session_trace.txt       # Human-readable trace
        │   ├── metadata.json           # Task metadata
        │   ├── sandbox_output/         # Exported from sandbox
        │   └── neo4j_history/          # Neo4j database export
        └── task_002/
            └── ...
```

## Pages in This Section

- [Workflow Details](workflow.md): step-by-step internals of how each evaluation runs.
- [Built-in Benchmarks](built-in-benchmarks.md): concrete CLI invocations for CyberGym, SWE-Bench Pro, and SeCodePLT.
- [Adding a Benchmark](adding-evaluations.md): how to create a new benchmark.
