# NYU CTF Bench

This package adds an evaluation for [NYU_CTF_Bench](https://github.com/NYU-LLM-CTF/NYU_CTF_Bench) and exports leaderboard-ready artifacts for [leaderboard_submissions](https://github.com/NYU-LLM-CTF/leaderboard_submissions).

## Prerequisites

Install `nyuctf` package and get the artifacts:

```bash
uv pip install nyuctf
uv run python -m nyuctf.download
```

Two challenges need extra steps:

- `2023f-for-forensings` needs to download extra artifacts from [google drive](https://drive.google.com/file/d/1ir5S2c42ACzXLma8j69RvrHOBNo_2ILg).
- `2023f-web-rainbox-notes` should append the following into `chrome.json`

```json
{
  "name": "fsconfig",
  "action": "SCMP_ACT_ALLOW",
  "args": null
},
{
  "name": "fsmount",
  "action": "SCMP_ACT_ALLOW",
  "args": null
},
{
  "name": "fsopen",
  "action": "SCMP_ACT_ALLOW",
  "args": null
},
{
  "name": "fspick",
  "action": "SCMP_ACT_ALLOW",
  "args": null
},
{
  "name": "move_mount",
  "action": "SCMP_ACT_ALLOW",
  "args": null
},
{
  "name": "open_tree",
  "action": "SCMP_ACT_ALLOW",
  "args": null
},
{
  "name": "statx",
  "action": "SCMP_ACT_ALLOW",
  "args": null
},
```

Then, create the shared Docker network used by the benchmarked agent and the challenge services:

```bash
docker network create ctfnet
```

## Run a single challenge

```bash
uv run python -m benchmarks.nyuctf.nyuctf_bench run_debug \
  --challenge_name 2021f-rev-maze
```

## Run a batch with the default bundled agent

```bash
uv run python -m benchmarks.nyuctf.nyuctf_bench run
```

You can also specity the following arguments to customize the run:

- `--output_dir`: output directory for the batch run (defaults to `evals/nyu_ctf_bench/<timestamp>`)
- `--max_workers`: default to 1
- `--submission_agent`: submission agent for the batch run (defaults to `opensage-ctf`)
- `--submission_model`: submission model for the batch run

```bash
uv run python -m benchmarks.nyuctf.nyuctf_bench run \
  --max_workers 2 \
  --submission_agent opensage-ctf \
  --submission_model claude-opus-4-6
```

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
- The default agent target is `examples/agents/ctf_agent`, but `--agent_dir` can point to any compatible agent package.
