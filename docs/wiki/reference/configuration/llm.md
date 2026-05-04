# `[llm]` Reference

Field reference for the `[llm]` section. For how to use it, see the [LLM configuration guide](../../get-started/customize/llm.md).

## Model Profiles

Models are defined under `[llm.model_configs.<profile>]`. Recognized profile names:

| Profile | Purpose |
|---------|---------|
| `main` | Primary model for agent reasoning |
| `summarize` | Summarization and context compression |
| `flag_claims` | Flag-claims processing |

You can add further custom profiles; subsystems that know a profile name will use it, otherwise they fall back to `main`.

## Per-Profile Fields

Each `[llm.model_configs.<profile>]` block accepts:

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `model_name` | `string` | Model identifier (e.g., `"anthropic/claude-opus-4-7"`, `"openai/gpt-5.4"`) | Required |
| `temperature` | `float` | Sampling temperature (0.0–2.0) | `None` |
| `max_tokens` | `integer` | Maximum tokens in response | `None` |
| `rpm` | `integer` | Rate limit: requests per minute | `None` |
| `tpm` | `integer` | Rate limit: tokens per minute | `None` |
