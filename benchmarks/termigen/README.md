# TermiGen

This package adds an OpenSage evaluation for
[TermiGen / terminal-bench-env](https://github.com/ucsb-mlsec/terminal-bench-env),
a collection of 3,500+ Docker-based terminal tasks. TermiGen ships tasks in
Harbor 2.0 format under `environments_harbor/`, so this integration reuses
`HarborEvaluation` and only adds a thin loader that clones the upstream repo.

## Prerequisites

- Docker (the Harbor evaluation builds a per-task image and runs the agent
  inside it)
- `git` on `PATH`

The Harbor extras are required so the shared Harbor evaluation code is
importable:

```bash
uv pip install -e ".[harbor]"
```

## Prepare the dataset

Clone (or refresh) the upstream repo and print the resolved tasks directory:

```bash
uv run python -m benchmarks.termigen.termigen_bench prepare
```

By default the repo is cloned to `~/.cache/opensage/termigen/terminal-bench-env`.
Override with `--install_dir`.

## Run a single task

Pass a one-line task-ids file to restrict the run:

```bash
echo hello_world_medium > /tmp/termigen_task.txt
uv run python -m benchmarks.termigen.termigen_bench run \
    --task_file /tmp/termigen_task.txt
```

## Run the full batch

```bash
uv run python -m benchmarks.termigen.termigen_bench run
```

Common flags (all inherited from `HarborEvaluation`):

- `--output_dir`: where to write per-task outputs (defaults to
  `evals/termigen/<timestamp>`)
- `--agent_dir`: agent package; defaults to `examples/agents/harbor_agent`
- `--max_workers`: parallel task runners (default 1)
- `--start_idx` / `--end_idx`: slice the task list
- `--skip_existing`: skip tasks whose output directory already exists
- `--test_timeout`: seconds per `tests/test.sh` invocation
  (per-task `task.toml` still wins)

Pin to a specific upstream revision:

```bash
uv run python -m benchmarks.termigen.termigen_bench run \
    --repo_ref main --install_dir ~/.cache/opensage/termigen
```

## Output layout

Same as the Harbor evaluation:

```text
<output_dir>/
├── <task_id>/
│   ├── score.json          # not written by Harbor; test_result.json is authoritative
│   ├── test_result.json    # pass/fail + test.sh output
│   ├── session_trace.json
│   └── sandbox_output/
└── evaluation_results.json
```

## Notes

- Only the Harbor 2.0 tasks under `environments_harbor/` are wired up.
  The `termigen_env.zip` (TerminalBench 1.0 format) is ignored.
- Each task's container image is built on demand (`harbor_<task_id>`); the
  build cache persists across runs.
