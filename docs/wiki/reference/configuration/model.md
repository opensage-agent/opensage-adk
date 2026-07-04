# `[model]` Reference

Field reference for the `[model]` section, the model registry and the session-wide runtime budget. For how to pick and configure models, see the [LLM configuration guide](../../get-started/customize/llm.md).

The registry accepts two mutually exclusive sources. Declare models inline with `available_models` for simple LiteLLM setups, or point `models_python_file` at a Python file that exports `models: list[BaseLlm]` when a model needs custom LiteLLM kwargs such as `cache_control_injection_points`. Setting both raises an error when the registry loads.

## Fields

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `available_models` | `list[ModelEntry]` | Models declared inline (see below). Each `model` value is both the registry key and the id passed to `LiteLlm(model=...)`; duplicates raise. | `[]` |
| `models_python_file` | `string` | Path to a Python file exporting `models: list[BaseLlm]`. Use instead of `available_models`. | `None` |
| `budget` | `float` | Runtime token budget in USD shared by every model call in one session. `0` or omitted means unlimited. | `0.0` |
| `prices` | `list[ModelPrice]` | Custom per-model prices for the runtime budget (see below). | `[]` |
| `evaluation_replace_all_models_with_model_name` | `string` | When set, evaluation replaces every agent's model with the registered model under this name. `None` keeps each agent's declared model. | `None` |

## `available_models` Entry (`ModelEntry`)

Each entry under `[[model.available_models]]`:

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `model` | `string` | LiteLLM-style id (`<provider>/<model>`), also the registry key | required |
| `api_key_env` | `string` | Environment variable that holds the API key | required |
| `base_url` | `string` | Override the provider base URL | `None` |

## `prices` Entry (`ModelPrice`)

Each entry under `[[model.prices]]`. Prices are USD per one million tokens.

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `model` | `string` | Registry key this price applies to | required |
| `prompt_per_million` | `float` | Price per 1M prompt tokens | required |
| `completion_per_million` | `float` | Price per 1M completion tokens | required |
| `cached_per_million` | `float` | Price per 1M cached prompt tokens; defaults to `prompt_per_million` when omitted | `None` |

## Example

```toml title="config.toml"
[model]
budget = 5.0

[[model.available_models]]
model = "anthropic/claude-sonnet-4-5-20250929"
api_key_env = "ANTHROPIC_API_KEY"

[[model.available_models]]
model = "openai/gpt-5"
api_key_env = "OPENAI_API_KEY"

[[model.prices]]
model = "openai/gpt-5"
prompt_per_million = 1.25
completion_per_million = 10.0
```
