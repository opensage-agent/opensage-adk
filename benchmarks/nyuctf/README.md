# NYU CTF Benchmark

Run NYU_CTF_Bench challenges with either the OpenSage SageCTF agent or standalone Claude Code / Codex baselines.

## Prerequisites

Clone the forked benchmark repository:

```bash
git clone https://github.com/ziyue-pan/NYU_CTF_Bench
```

After cloning, enter the repository and pull the large objects:

```bash
cd NYU_CTF_Bench
git lfs pull
```

## SageCTF

Run

```bash
uv run python -m benchmarks.nyuctf.sagectf run \
  --bench_dir /path/to/NYU_CTF_Bench \
  --agent_dir /path/to/agent_dir \
  --output_dir evals/nyuctf
```

Useful options:

```text
--max_workers 1
--time_limit 12h
--budget 100
```

Benchmark-related options:

```text
--challenge_name 2021f-rev-maze
--dataset_json /path/to/test_dataset.json
--challenge_name id1,id2
--max_challenges N
```

Each challenge writes `score.json`, `session_trace.json`, `sandbox_output/`, and `submission_trajectory/<challenge_name>.json` under its output directory. Batch runs also write `results/evaluation_results.json` and `results/leaderboard_submission/`.

## Baseline: Claude Code

Prepare the Claude image credentials:

```bash
cp benchmarks/nyuctf/claude-image/.env.template benchmarks/nyuctf/claude-image/.env
```

Run a batch:

```bash
uv run python -m benchmarks.nyuctf.baseline run \
  --agent claude-code \
  --bench_dir /path/to/NYU_CTF_Bench \
  --time_limit 6h \
  --budget 100 \
  --output_dir evals/nyuctf-claude
```

Useful options:

```text
--time_limit 6h
--budget 100
--max_workers 1
--dataset_json /path/to/test_dataset.json
--challenge_name 2021f-rev-maze
--max_challenges N
```

The Claude Code baseline builds from `benchmarks/nyuctf/claude-image`, defaults to `claude-opus-4-8`, uses reasoning effort `high`, and applies a `100` USD per-challenge budget. The image starts the gdb, IDA, and pyghidra MCP services before invoking Claude Code.

## Baseline: Codex

Prepare the Codex image credentials and config:

```bash
cp benchmarks/nyuctf/codex-image/auth.json.template benchmarks/nyuctf/codex-image/auth.json
cp benchmarks/nyuctf/codex-image/config.toml.template benchmarks/nyuctf/codex-image/config.toml
```

Run a batch:

```bash
uv run python -m benchmarks.nyuctf.baseline run \
  --agent codex \
  --bench_dir /path/to/NYU_CTF_Bench \
  --time_limit 6h \
  --budget 100 \
  --output_dir evals/nyuctf-codex
```

Useful options:

```text
--time_limit 6h
--budget 100
--max_workers 1
--dataset_json /path/to/test_dataset.json
--challenge_name 2021f-rev-maze
--max_challenges N
```

The Codex baseline builds from `benchmarks/nyuctf/codex-image`, defaults to `gpt-5.5`, uses reasoning effort `high`, and applies a `100` USD per-challenge budget. The runner copies `auth.json` and `config.toml` into `/root/.codex/` inside each Codex container before start; the entrypoint starts the custom gdb SSE MCP server and connects it through `mcp-remote`, while IDA and pyghidra are registered through their streamable HTTP `/mcp` endpoints. Codex budget control is enforced by the runner from streamed usage/cost data; `--time_limit` is also enforced by the runner and kills the process/container when exceeded.
