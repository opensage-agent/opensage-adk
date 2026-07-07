# Cybench Benchmark

Run Cybench CTF tasks with either the OpenSage SageCTF agent or standalone Claude Code / Codex baselines.

## Prerequisites

Clone Cybench locally:

```bash
git clone git@github.com:ziyue-pan/cybench.git
```

## SageCTF

Run

```bash
uv run python -m benchmarks.cybench.sagectf run \
  --bench_dir ../cybench \
  --agent_dir ../sagectf/ctf_agent \
  --output_dir evals/cybench
```

Useful options:

```text
--max_workers 2
--time_limit 6h
--budget 100
```

Benchmark-related options:

```text
--task_list task_list.txt \
--challenge_name id1,id2
--max_challenges N
--reuse_sandbox_images False
```

Each task writes `score.json`, `session_trace.json`, `live_events.jsonl`, `cost_info.json`, `raw/`, and `submission_trajectory/<canonical_name>.json` under its output directory. Reusing the same `--output_dir` skips completed tasks.

## Baseline: Claude Code

Prepare the Claude image credentials:

```bash
cp benchmarks/cybench/claude-image/.env.template benchmarks/cybench/claude-image/.env
```

Run:

```bash
uv run python -m benchmarks.cybench.baseline run \
  --agent claude-code \
  --bench_dir ../cybench \
  --time_limit 6h \
  --budget 100 \
  --output_dir evals/cybench-claude
```

Useful options:

```text
--time_limit 6h
--budget 100
--max_workers 1
--task_list task_list.txt
--challenge_name benchmark/path/or/canonical_name
--max_challenges N
```

The Claude Code baseline builds from `benchmarks/cybench/claude-image`, defaults to `claude-opus-4-8`, uses reasoning effort `high`, and applies a `100` USD per-task budget. The image starts the gdb, IDA, and pyghidra MCP services before invoking Claude Code.

## Baseline: Codex

Prepare the Codex image credentials and config:

```bash
cp benchmarks/cybench/codex-image/auth.json.template benchmarks/cybench/codex-image/auth.json
cp benchmarks/cybench/codex-image/config.toml.template benchmarks/cybench/codex-image/config.toml
```

Run:

```bash
uv run python -m benchmarks.cybench.baseline run \
  --agent codex \
  --bench_dir ../cybench \
  --time_limit 6h \
  --budget 100 \
  --output_dir evals/cybench-codex
```

Useful options:

```text
--time_limit 6h
--budget 100
--max_workers 1
--task_list task_list.txt
--challenge_name benchmark/path/or/canonical_name
--max_challenges N
```

The Codex baseline builds from `benchmarks/cybench/codex-image`, defaults to `gpt-5.5`, uses reasoning effort `high`, and applies a `100` USD per-task budget. The runner copies `auth.json` and `config.toml` into `/root/.codex/` inside each Codex container before start; the entrypoint starts the custom gdb SSE MCP server and connects it through `mcp-remote`, while IDA and pyghidra are registered through their streamable HTTP `/mcp` endpoints. Codex budget control is enforced by the runner from streamed usage/cost data; `--time_limit` is also enforced by the runner and kills the process/container when exceeded.
