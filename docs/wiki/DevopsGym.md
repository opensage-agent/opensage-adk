# DevOps-Gym

[DevOps-Gym](https://github.com/ucsb-mlsec/DevOps-Gym) evaluates agents across the full DevOps
cycle: build & configuration, monitoring, issue resolution, test generation, and end-to-end
pipelines (704 tasks total from 30+ real-world Java/Go projects).

## Prerequisites

```bash
# Clone DevOps-Gym once (not a submodule — run this from the project root)
git clone https://github.com/ucsb-mlsec/DevOps-Gym third_party/devops-gym
```

## Run a single task for debugging

```bash
python benchmarks/devopsgym/devops_gym.py run_debug \
  --task_category build \
  --start_idx 0 \
  --end_idx 1
```

## Run a full category in parallel

```bash
python benchmarks/devopsgym/devops_gym.py run \
  --task_category build \
  --max_workers 4

python benchmarks/devopsgym/devops_gym.py run \
  --task_category issue_resolving \
  --max_workers 6 \
  --skip_existing
```

## Run specific tasks from a file

```bash
# tasks.txt: one task_id per line
python benchmarks/devopsgym/devops_gym.py run \
  --task_category build \
  --task_file tasks.txt \
  --max_workers 2
```

## Re-run evaluation only (aggregate already-completed results)

```bash
python benchmarks/devopsgym/devops_gym.py evaluate \
  --output_dir evals/devopsgym/<timestamp>
```

## Task categories

| Category | Tasks | Description |
|----------|-------|-------------|
| `build` | 48 | Fix build failures and dependency issues |
| `monitor` | 30 | Diagnose runtime anomalies (CPU, memory, disk, I/O) |
| `issue_resolving` | 310 | Generate patches for real GitHub issues |
| `test_generation` | 310 | Write tests that validate issue fixes |
| `end_to_end` | 14 | Multi-stage DevOps pipelines |

## Output structure

```
evals/devopsgym/<timestamp>/
├── <task_id>/
│   ├── automated_grade.json   # pass/fail + run-tests.sh stdout
│   ├── sandbox_output/        # /workspace contents exported from container
│   ├── session_trace.json     # full agent conversation trace
│   └── metadata.json
└── results/
    └── evaluation_results.json  # aggregated success rate + per-category stats
```

## Design notes

- **Answer leakage prevention**: `run-tests.sh` is injected into the container *after* the
  agent session ends, so the agent never sees the evaluation script.
- **Monitor tasks**: `start.sh` (if present) is automatically run in the background inside
  the container before the agent starts, setting up the service to be observed.
- The agent uses `examples/agents/devops_gym_agent/` — a lightweight single-sandbox agent
  with no Neo4j dependency.
