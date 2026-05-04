# NYU CTF Bench

This package adds an evaluation for [NYU_CTF_Bench](https://github.com/NYU-LLM-CTF/NYU_CTF_Bench) and exports leaderboard-ready artifacts for [leaderboard_submissions](https://github.com/NYU-LLM-CTF/leaderboard_submissions).

The OpenSage evaluation driver ships in this directory.

## Layout

```
benchmarks/nyuctf/
├── README.md
├── __init__.py
├── helpers.py       # dataset loading, prompts, scoring, and judge helpers
└── nyuctf_bench.py  # OpenSage evaluation driver
```

## Prerequisites

Clone the forked [NYU_CTF_Bench](https://github.com/ziyue-pan/NYU_CTF_Bench) repository — only the dataset itself is needed; the bundled `nyuctf` Python package is not required, the loader reads `<repo>/test_dataset.json` and each `challenge.json` directly.

Then, create the shared Docker network used by the benchmarked agent and the challenge services:

```bash
docker network create ctfnet
```

You can either pass `--repository_dir /path/to/NYU_CTF_Bench` to each command below, or set it once via environment:

```bash
export NYUCTF_REPOSITORY_DIR=/path/to/NYU_CTF_Bench
```

Any benchmark following the same layout (a top-level dataset JSON of `canonical_name → {path}` plus per-challenge `challenge.json` files) can be loaded by passing `--dataset_json /path/to/<name>_dataset.json` instead.

### Run a single challenge

```bash
uv run python -m benchmarks.nyuctf.nyuctf_bench run_debug --repository_dir /path/to/NYU_CTF_Bench \
  --challenge_name 2021f-rev-maze
```

### Run a batch with the default bundled agent

```bash
uv run python -m benchmarks.nyuctf.nyuctf_bench run --repository_dir /path/to/NYU_CTF_Bench --output_dir /path/to/output_dir
```

You can also specity the following arguments to customize the run:

- `--repository_dir`: local checkout of a benchmark repo; the loader picks up `<repo>/<split>_dataset.json`
- `--dataset_json`: explicit dataset JSON path; takes precedence over `--repository_dir`
- `--output_dir`: output directory for the batch run (defaults to `evals/nyu_ctf_bench/<timestamp>`)
- `--max_workers`: default to 1 (current NYU_CTF_Bench challenges have race conditions on ports)
- `--submission_agent`: submission agent for the batch run (defaults to `opensage-ctf`)
- `--submission_model`: submission model for the batch run

> [!IMPORTANT]
> You can resume the batch run by running the same command again with the same `--output_dir`. OpenSage will skip challenges whose output directories are already present, so only missing ones will be scheduled for evaluation.

## Output layout

The run directory contains normal OpenSage task outputs plus a submission bundle:

```text
/<output_dir>/
├── <challenge_name>/
│   ├── score.json                <- per-challenge score
│   ├── session_trace.json
│   ├── sandbox_output/
│   └── submission_trajectory/
│       └── <challenge_name>.json
└── results/
    ├── evaluation_results.json
    └── leaderboard_submission/
        ├── summary.json
        └── <challenge_name>.json
```

`results/leaderboard_submission/summary.json` follows the NYU_CTF_Bench submission format, and the per-challenge trajectory JSON files are written directly into `results/leaderboard_submission/`.

## Notes

- Challenge services are launched with `docker compose` and then attached to `ctfnet` so the benchmark sandbox can reach them.
- The prompt instructs the agent to both print the candidate flag and write it to `/workspace/final_flag.txt`.
- The default OpenSage agent target is `examples/agents/ctf_agent`, but `--agent_dir` can point to any compatible agent package.
