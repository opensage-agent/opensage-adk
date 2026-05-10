# LLM Configuration

**Section:** `[llm]`

This is the one section almost every agent overrides: it tells OpenSage-ADK which model to use, what provider it lives behind, and the sampling knobs. Every agent needs at least a `main` model; additional **profiles** let you point individual subsystems (summarization, claim-flagging, and similar) at different models without changing code.

## Defining a Model

Each profile lives under `[llm.model_configs.<profile>]`. The `main` profile is the one every agent uses for reasoning; `summarize` is optional and falls back to `main` if absent.

```toml title="config.toml"
[llm.model_configs.main]
model_name = "anthropic/claude-opus-4-7"
temperature = 0.7
max_tokens = 4096
```

## Multiple Profiles

Use a cheaper or smaller model for summarization than for reasoning:

```toml title="config.toml"
[llm.model_configs.main]
model_name = "openai/gpt-5.4"
temperature = 0.7
max_tokens = 4096
rpm = 60
tpm = 60000

[llm.model_configs.summarize]
model_name = "openai/gpt-4o"
temperature = 0.3
max_tokens = 2048
rpm = 30
tpm = 30000
```

!!! info "Authentication"
    OpenSage-ADK uses LiteLLM under the hood. Set `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc. in your environment; LiteLLM resolves credentials from the provider prefix (`openai/…`, `anthropic/…`) automatically.

---

See the [`[llm]` field reference](../../reference/configuration/llm.md) for the full list of per-profile fields and accepted profile names.
