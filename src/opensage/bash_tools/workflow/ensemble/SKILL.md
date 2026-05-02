---
name: ensemble
description: "Run the same task on multiple agents/models in parallel and reduce the answers (majority vote or disagreement check). Replaces the legacy agent_ensemble tool."
---

# Ensemble Pattern

Same question, multiple independent subagents (typically with different models), then reduce. Useful when you want N opinions on a high-stakes call and there's no ground truth — e.g. "is this function vulnerable?".

## Usage

```
models = get_available_models()           # pick 2-5 distinct ones
sids = []
for m in chosen_models:
    r = call_subagent(agent_name="X", request=Q, mode="async", model_name=m)
    sids.append(r.session_id)

for sid in sids:
    wait_for_subagent(sid, timeout=...)

# Read each subagent's final reply from your inbox (each async invocation
# posts its result back as kind="result"). Then majority vote / compare.
```

Key points:

- **`mode="async"` is required** for parallelism. Sync mode serializes the calls.
- All chosen models must be in `get_available_models()`. Unregistered → `KeyError`.
- Cost scales linearly with N. Don't ensemble cheap-easy queries.
- Each async result lands in the **caller's** inbox; read it after `wait_for_subagent`.

## Common Use Cases

- Vulnerability triage: 3 models say yes, 1 says no → flag for review
- Code review on subtle correctness questions
- Disagreement detection between models on the same prompt

## Requires Sandbox

None — pure orchestration.
