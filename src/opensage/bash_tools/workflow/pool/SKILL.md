---
name: pool
description: "Run N tasks under a concurrency cap K via a sliding-window worker pool. Use when many subagent invocations would otherwise hit provider rate limits."
---

# Pool Pattern

Many tasks (say 20) to run via subagents, but provider RPM/TPM (or memory) caps how many can run at once (say 6). Maintain a sliding window of K active subagents.

## Usage

```
tasks = [task_1, task_2, ..., task_N]
K = 6                          # concurrency cap
active = {}                    # sid -> task_id
done = {}                      # task_id -> final result text

while tasks or active:
    # Fill the pool up to K
    while tasks and len(active) < K:
        t = tasks.pop(0)
        r = call_subagent(agent_name="worker", request=t.prompt, mode="async")
        active[r.session_id] = t.id

    # Short-poll for any one to finish, then drain
    for sid in list(active.keys()):
        r = wait_for_subagent(sid, timeout=1.0)
        if r.success and r.state in ("sleeping", "unloaded"):
            done[active[sid]] = read_inbox_for(sid)
            del active[sid]
            break  # restart fill loop
```

Key points:

- **No first-completed multi-wait primitive yet.** `wait_for_subagent` with a small timeout polls; loop until one finishes.
- **K depends on the provider.** Hosted Anthropic / OpenAI vs local LiteLLM proxy → very different limits. Start with 4-6.
- **Order of completion is unpredictable.** Don't assume done-order matches submission order.
- **Failed tasks free their slot like any other completion.** Retry failed ones in a separate pass, or feed them into the multi-model pattern.

## Common Use Cases

- Batch evaluation: N CTF / SWE-bench tasks on one orchestrator
- Bulk code review on many files
- Fan-out exploration when each branch is independent

## Requires Sandbox

None — pure orchestration.
