# AReaL Training

## Quick Start

```bash
# From OpenSage root (auto-detects AReaL as sibling directory)
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
3. `../AReaL` (sibling directory to OpenSage)

## Setup (one-time)

```bash
# Clone AReaL with OpenSage as submodule
git clone --recurse-submodules -b adk https://github.com/rucnyz/AReaL
cd AReaL
uv sync --extra cuda
```

## Key Files (in AReaL repo)

| File | Role |
|------|------|
| `examples/opensage/opensage_grpo_mt.yaml` | Training config (model, generation_kwargs, etc.) |
| `examples/opensage/run_opensage_grpo.sh` | AReaL-side launch script |
| `examples/opensage/workflow.py` | RL workflow orchestration |
| `examples/opensage/opensage_rl_mt.py` | Entry point |

## Docs

See [AReaL-Training](../../docs/wiki/rl_integration/AReaL-Training.md) for full configuration and known issues.
