# AReaL Training

## Quick Start

```bash
# From AIgiSE root (auto-detects AReaL as sibling directory)
bash rl/areal/train.sh --trial my_experiment

# Or specify AReaL location
bash rl/areal/train.sh --areal-dir /path/to/AReaL --trial my_experiment

# 2-GPU mode
bash rl/areal/train.sh --gpus 2,3 --ngpu 2 --allocation sglang:d1p1t1+fsdp:d1p1t1
```

## AReaL Directory Resolution

The script looks for AReaL in this order:
1. `--areal-dir` argument
2. `AREAL_DIR` environment variable
3. `../AReaL` (sibling directory to AIgiSE)

## Setup (one-time)

```bash
# Clone AReaL with AIgiSE as submodule
git clone --recurse-submodules -b adk https://github.com/rucnyz/AReaL
cd AReaL
uv sync --extra cuda
```

## Key Files (in AReaL repo)

| File | Role |
|------|------|
| `examples/aigise/aigise_grpo_mt.yaml` | Training config (model, generation_kwargs, etc.) |
| `examples/aigise/run_aigise_grpo.sh` | AReaL-side launch script |
| `examples/aigise/workflow.py` | RL workflow orchestration |
| `examples/aigise/aigise_rl_mt.py` | Entry point |

## Docs

See [AReaL-Training](../../docs/wiki/AReaL-Training.md) for full configuration and known issues.
