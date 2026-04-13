# OpenSage + NeMo RL Integration

Run OpenSage agent tasks (including 60+ Harbor benchmarks) with NVIDIA NeMo RL's training pipeline.

## Architecture

```
NeMo RL (controls generation loop)          OpenSage Environment
┌──────────────────────────────┐
│ GRPO / DPO / DAPO training   │
│                              │
│ Generation (vLLM / SGLang)   │
│   → model generates response │           ┌─────────────────────────┐
│   → response has tool calls  ├──────────►│ OpenSageEnvironment     │
│                              │           │   1. Parse tool calls   │
│ Rollout loop (multi-turn)    │           │   2. Execute in Docker  │
│   ← observation returned     │◄──────────┤   3. Return observation │
│   → generate next response   │           │                         │
│   ...                        │           │ On termination:         │
│                              │           │   4. Run tests/test.sh  │
│ Token tracking (implicit)    │           │   5. Return reward      │
│ Policy update (Megatron/FSDP)│           └─────────────────────────┘
└──────────────────────────────┘
```

## Setup

```bash
# Register the environment in your NeMo RL training script
from opensage.evaluation.rl_adapters.nemo_rl_env import OpenSageEnvironment
from nemo_rl.environments.utils import register_env

register_env("opensage", "opensage.evaluation.rl_adapters.nemo_rl_env.OpenSageEnvironment")
```

## Config

Add to your NeMo RL YAML config:

```yaml
env:
  opensage:
    tasks_dir: swebench          # harbor registry name (auto-downloads)
    # tasks_dir: /data/my_tasks  # or local directory
    max_turns: 30
    test_timeout: 120

data:
  train:
    default:
      env_name: opensage
      file_path: /path/to/prompts.jsonl
```

## Usage

```bash
# With NeMo RL's standard entry point
python examples/run_grpo.py \
  --config examples/configs/my_opensage_config.yaml
```

## Supported Task Sources

Any Harbor-format task directory works:
- `swebench` — SWE-bench (auto-download)
- `compilebench` — CompileBench (auto-download)
- Local directories with Harbor task format
- Custom tasks with instruction.md + Dockerfile + test.sh
