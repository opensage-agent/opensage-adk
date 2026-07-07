# History

**Section:** `[history]`

Controls how tool outputs and event history are kept under control during a session. Two levers:

1. **Truncate individual tool responses** before they enter the history, so one enormous output cannot swamp everything else.
2. **Compact the whole event history** once its cumulative size crosses a threshold: a summarization pass keeps the trailing portion intact and replaces the older half with a compressed summary.

## Example

```toml title="config.toml"
[history]
max_tool_response_length = 10000
enable_quota_countdown = true

[history.events_compaction]
max_history_summary_length = 100000
compaction_percent = 50
```

- `max_tool_response_length` caps a single tool's output; anything longer is stored out-of-band and replaced with a short placeholder the model can still reason about.
- `max_history_summary_length` is the budget that triggers compaction.
- `compaction_percent` controls how much of the history gets summarized when compaction fires (50 means "summarize the older half, keep the newer half verbatim").

---

See the [`[history]` field reference](../../reference/configuration/history.md) for all top-level and `events_compaction` fields.
