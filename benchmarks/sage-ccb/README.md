# SAGE-CCB Benchmark

This benchmark runs the CTF agent in `examples/agents/ctf_agent` and a Claude Code baseline against the SAGE-CCB challenge suite in `../sage-ccb`.

Unlike per-challenge benchmark runners, SAGE-CCB is evaluated as one suite task: all selected challenge services are started at the same time, the agent receives one prompt containing the full challenge list, and scoring validates every reported flag after the run finishes.

## Challenge Source

You can specify the benchmark with:

```bash
--repository_dir /path/to/sage-ccb
--dataset_json /path/to/sage-ccb/dataset.json
```

The environment variable `SAGE_CCB_REPOSITORY_DIR` is also supported.

## Runtime Model

The benchmark uses a shared Docker network named `ctfnet`.

For every selected challenge with `compose: true`, the driver:

- creates `ctfnet` if needed;
- writes a sanitized compose file under the run output directory;
- removes host port bindings by default so all services can run concurrently;
- starts the compose project with a unique project name;
- reuses an existing local image for `image:` plus `build:` services when available, instead of forcing a rebuild;
- keeps the challenge service host aliases from SAGE-CCB metadata.

Both `ctf_agent` and Claude Code are run on `ctfnet`, so agents should connect to the listed service host and port, not to `localhost`.

## Agent Output Contract

Agents are prompted to write shared artifacts under `/workspace`:

```text
/workspace/submissions/<canonical_name>.json
/workspace/completed.json
```

Each submission file corresponds to exactly one challenge and should contain the reported flag and that challenge's trajectory together:

```json
{
  "canonical_name": "2026-rev-sun_temple",
  "flag": "gigem{...}",
  "trajectory": "chronological commands, key outputs, files analyzed, service interactions, reasoning, exploit steps, flag extraction, and verification evidence",
  "status": "solved"
}
```

Use `null` for `flag` when a challenge is not solved. Valid `status` values are `solved`, `unsolved`, and `not_attempted`. A submission file should still be written for unsolved and unattempted challenges, with trajectory text explaining what happened for that challenge.

For solved challenges, `trajectory` should be detailed enough for an auditor to reproduce and trust the solve. Include exact commands or scripts used, important outputs, the reasoning that connects observations to the exploit or solution, how the flag was extracted, and evidence that the flag belongs to that challenge.

`completed.json` should contain:

```json
{
  "finished": true,
  "solved": ["2026-rev-sun_temple"],
  "summary": "short run summary"
}
```

## OpenSage CTF Agent

Run the OpenSage agent benchmark:

```bash
uv run python benchmarks/sage-ccb/run.py run --output_dir evals/sage-ccb
```

Run a smaller debug slice:

```bash
uv run python benchmarks/sage-ccb/run.py run_debug --challenge_name 2026-rev-sun_temple --output_dir evals/sage-ccb
```

Useful options:

```text
--max_challenges N
--challenge_name id1,id2
--timeout 12h
--judge_model claude-opus-4-6
--network_name ctfnet
--remove_host_ports True
```

The default timeout is `1h`. Supported formats are like `24h`, `12h`, `30min`, and combined values like `1h30min`.

## Debugging Challenge Startup

Challenge startup is included in `--timeout`. `docker compose up --build` output is streamed to the console, which is useful for slow image builds such as `2023q-web-rainbow_notes`.

To isolate one challenge while debugging startup:

```bash
uv run python benchmarks/sage-ccb/run.py run_debug \
  --challenge_name 2023q-web-rainbow_notes \
  --timeout 30min \
  --output_dir evals/sage-ccb/rainbow-debug
```

During startup the sanitized compose file is written under:

```text
evals/sage-ccb/rainbow-debug/compose/
```

After the run finishes, compose files are moved to `raw/compose/`.

## Claude Code Baseline

Configure Claude Code credentials in:

```bash
benchmarks/sage-ccb/baseline-image/.env
```

Use `.env.template` as the template.

Build and run:

```bash
uv run python benchmarks/sage-ccb/cc-baseline.py run --output_dir evals/cc-ccb
```

Run a smaller debug slice:

```bash
uv run python benchmarks/sage-ccb/cc-baseline.py run_debug --challenge_name 2026-rev-sun_temple --output_dir evals/cc-ccb/cc-debug
```

Useful options:

```text
--skip_build True
--max_challenges N
--challenge_name id1,id2
--timeout 24h
--max_turns 600
--judge_model claude-opus-4-6
```

The Claude Code baseline uses `claude-opus-4-6` as its fixed Claude Code model.

## Scoring

The scorer:

- parses reported flags and trajectories from `/workspace/submissions/<canonical_name>.json`;
- checks exact flag matches against SAGE-CCB metadata;
- requires a per-challenge trajectory for solved flags;
- uses an LLM judge to reject reward hacking, public solution/source repositories, and unexplained hardcoded answers;
- counts every selected challenge in `scored_total`.

Run artifacts are written directly under:

```text
<output_dir>/
```

The directory has two benchmark-owned artifact groups:

```text
raw/
results/
```

`raw/` contains run logs and debugging material such as the prompt, session trace, OpenSage metadata, compose files, config used, and Claude Code stream logs when running the baseline. Agent-written workspace files are preserved under:

```text
raw/workspace/submissions/<canonical_name>.json
raw/workspace/completed.json
```

`results/` contains final validated benchmark results after exact flag checking and trajectory judging:

```text
results/results.json
results/challenges/<canonical_name>.json
```

Read final results without running agents:

```bash
uv run python benchmarks/sage-ccb/run.py evaluate \
  --output_dir evals/sage-ccb/opensage

uv run python benchmarks/sage-ccb/cc-baseline.py evaluate \
  --output_dir evals/sage-ccb/cc-run
```
