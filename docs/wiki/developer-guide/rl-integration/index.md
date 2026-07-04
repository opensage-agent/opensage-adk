---
icon: lucide/graduation-cap
---

# RL Training Integration

OpenSage-ADK exposes evaluation benchmarks as RL rollout targets. This guide covers two integrations, AReaL and slime. A third adapter, `miles`, is registered in `src/opensage/evaluation/rl_adapters/adapters/` but does not yet have a dedicated guide.

| Framework | Rollout Engine | Trainer | Benchmark Coverage |
|---|---|---|---|
| [AReaL Training](areal.md) | SGLang (TP=2) | FSDP (DP=2), GRPO | SeCodePLT |
| [slime Training](slime.md) | SGLang in a SLIME container | Megatron-LM, SLIME | SeCodePLT, mock |

Both setups wrap OpenSage agents as the rollout environment and thread trajectories back to the trainer.
