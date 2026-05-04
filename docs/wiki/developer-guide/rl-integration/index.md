---
icon: lucide/graduation-cap
---

# RL Training Integration

OpenSage-ADK exposes evaluation benchmarks as RL rollout targets. Two integrations currently ship:

| Framework | Rollout Engine | Trainer | Benchmark Coverage |
|---|---|---|---|
| [AReaL Training](areal.md) | SGLang (TP=2) | FSDP (DP=2), GRPO | SeCodePLT |
| [slime Training](slime.md) | SGLang in a SLIME container | Megatron-LM, SLIME | SeCodePLT, mock |

Both setups wrap OpenSage agents as the rollout environment and thread trajectories back to the trainer.
