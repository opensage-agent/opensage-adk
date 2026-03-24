# RL Training

OpenSage currently supports RL training through two frameworks:

| Framework                  | Status | Docs                                             |
|----------------------------|--------|--------------------------------------------------|
| **AReaL** (ADK-based)      | Active | [AReaL-Training](../docs/wiki/rl_integration/AReaL-Training.md) |
| **SLIME** (Megatron-based) | Active | [slime-Training](../docs/wiki/rl_integration/slime-Training.md) |

## AReaL

AReaL uses an ADK (Agent Development Kit) integration. Launch from OpenSage:

```bash
bash rl/areal/train.sh --trial my_experiment
```

```
rl/areal/
├── train.sh            # Launcher (auto-detects AReaL as sibling dir)
└── README.md           # Setup and configuration
```

See [`rl/areal/README.md`](areal/README.md) for details.

## SLIME

SLIME uses a Megatron-LM based pipeline running inside a Docker container:

```bash
bash rl/slime/train.sh --benchmark secodeplt
```

```
rl/slime/
├── train.sh            # Launcher script
├── configs/
│   ├── debug.yaml      # Small-scale testing
│   └── secodeplt.yaml  # SeCodePLT training
└── mock_agent/         # Mock agent for testing
```

See [`rl/slime/` README](../docs/wiki/rl_integration/slime-Training.md) for setup and usage.
