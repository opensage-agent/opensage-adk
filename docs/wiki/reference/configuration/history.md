# `[history]` Reference

Field reference for the `[history]` section. For how to use it, see the [History configuration guide](../../get-started/customize/history.md).

## Top-Level Fields

Fields directly under `[history]`.

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `max_tool_response_length` | `integer` | Maximum length of a single tool response before special handling | `10000` |
| `enable_quota_countdown` | `boolean` | Show remaining LLM call quota after each tool response | `false` |

## Events Compaction

Fields under `[history.events_compaction]`. Control when and how the event history is summarized once it grows past a size threshold.

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `max_history_summary_length` | `integer` | Character budget threshold for triggering compaction | `100000` |
| `compaction_percent` | `integer` | Percentage of history to compress (0–100) | `50` |
