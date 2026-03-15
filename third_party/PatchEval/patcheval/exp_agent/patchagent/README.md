# Running PatchAgent Baseline on PatchEval

This folder contains a PatchAgent-native baseline for PatchEval.
It uses PatchAgent's core architecture (`PatchTask + Builder + agent_generator`) and adapts runtime validation to PatchEval Docker environments.

## What this baseline does

- Creates a `PatchEvalBuilder` adapter that implements the PatchAgent `Builder` interface.
- Runs `PatchTask.initialize()` to verify vulnerability reproducibility in PatchEval runtime.
- Runs `PatchTask.repair(agent_generator(...))` to generate patches using PatchAgent agents.
- Runs `PatchTask.validate(...)` in PatchEval runtime (`fix-run.sh` + `unit_test.sh`).
- Saves per-CVE patches and execution metadata.
- Converts outputs to PatchEval evaluation format and runs `patcheval/evaluation/run_evaluation.py`.

## Prerequisites

```bash
pip install -e ../PatchAgent

pip install -r requirements.txt
```

- You configured API env vars (for example by sourcing `../PatchAgent/.env`).

## Quick start (exp1 style)

From `PatchEval/patcheval/exp_agent/patchagent`:

```bash
# one-time repository preparation (recommended)
bash shells/prepare_repos.sh
```

```bash
bash shells/run_exp1.sh gpt-4o-mini 4
```

Then evaluate:

```bash
bash shells/run_eval.sh gpt-4o-mini_exp1 4
```

## Full experiment scripts

- `shells/run_exp1.sh`: default setup (`dataset.jsonl`).
- `shells/run_exp2.sh`: default dataset + 2 rounds (feedback injection between rounds).
- `shells/run_exp3.sh`: no location + 2 rounds.
- `shells/run_exp4.sh`: no knowledge.
- `shells/run_exp5.sh`: blackbox style (no location).

## File layout

- `run_patchagent_baseline.py`: PatchAgent-native generation entrypoint.
- `patcheval_builder.py`: PatchEval-to-PatchAgent Builder adapter.
- `evaluation/process_data.py`: convert generated patches to evaluator input JSONL.
- `shells/run_exp*.sh`: generation scripts.
- `shells/run_eval.sh`: conversion + evaluation script.
- `extract_token.py`: token summary from baseline outputs.

## Notes

- This baseline is implemented inside PatchEval's `exp_agent` style and supports experiments 1~5.
- It requires CVE repositories under `patcheval/exp_llm/projects` (or pass `--local_repo_path`).
- `run_exp2/3` differ from `run_exp1/4/5` by enabling multi-round refinement (`--rounds 2`) with previous runtime feedback injected into the next round.
