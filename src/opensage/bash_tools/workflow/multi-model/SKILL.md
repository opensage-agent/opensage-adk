---
name: multi-model
description: "Recovery strategy when a subagent is stuck on a hard task. Two escape hatches: spawn a fresh-context instance with the same model, or retry with a different model from the registry."
---

# Multi-Model Retry Pattern

A subagent has clearly stalled — repeating the same wrong fix, hallucinating non-existent APIs, or exceeding a step budget. Two ways out: same model + clean history, or a different model.

## Usage

### 3a. Fresh-context retry (same model)

The current subagent's history has accumulated misleading state. A new instance with the **same agent_name and same model** but **no prior context** often makes different choices.

```
new_sid = call_subagent(
    agent_name="worker",
    request=f"{original_task}\n\nPrevious attempt explored {summary} "
            f"and got stuck. Try a different angle.",
    mode="sync",
    # No model_name → uses the agent's default model.
)
```

### 3b. Different-model retry

Some tasks fit certain models better — Claude vs GPT-5 differ on long chains, large diffs, MCP-style tool use, etc.

```
models = get_available_models()
new_sid = call_subagent(
    agent_name="worker",
    request=original_task,
    mode="sync",
    model_name=alternate_model,    # different from the original
)
```

### Combined recipe

1. Decide the original is stuck (no progress in N tool calls, or final answer is `complain`-shaped).
2. Try **3a** first (cheaper — same model, fresh context). One attempt.
3. Still failing → try **3b** with each alternate model in `get_available_models()`, sync mode, in sequence.
4. If multiple alternates also fail → emit `complain` with the accumulated evidence. Don't loop forever.

Key points:

- **Don't auto-retry on every failure.** Distinguish "stuck" from "task genuinely impossible". `complain("no info")` is signal, not a bug to retry through.
- **Cap retries** (e.g. ≤2 alternate models). Each retry costs LLM calls.
- For 3b, the new model still has to be in `get_available_models()`. If the registry only has one model, only 3a is available.

## Common Use Cases

- Single-task subagent has burned its step budget without convergence
- Model-specific regression: one model can't handle a certain syntax, another can
- High-value task where you want to "throw bigger model at it" only after the cheap one fails

## Requires Sandbox

None — pure orchestration.
