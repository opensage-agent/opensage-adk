# `[agent_ensemble]` Reference

Field reference for the `[agent_ensemble]` section. For how to use it, see the [Agent Ensemble configuration guide](../../get-started/customize/agent-ensemble.md).

## Fields

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `thread_safe_tools` | `list[string]` | Tool names that are thread-safe (can be called in parallel) | `[]` |
| `available_models_for_ensemble` | `list[string]` or `string` | Model names available for ensemble (can be a comma-separated string) | `[]` |
