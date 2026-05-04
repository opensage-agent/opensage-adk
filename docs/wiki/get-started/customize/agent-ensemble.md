# Agent Ensemble

**Section:** `[agent_ensemble]`

Configures multi-model ensemble execution: the agent fans the same sub-task out across several models in parallel and the caller sees the aggregated result. Useful when a single model is unreliable or when you want to cross-check decisions.

## Example

```toml title="config.toml"
[agent_ensemble]
thread_safe_tools = ["google_search", "read_file"]
available_models_for_ensemble = ["openai/gpt-4", "anthropic/claude-opus-4-7"]
```

- `available_models_for_ensemble` lists the models the agent can fan out to. Each is a LiteLLM-style identifier (`<provider>/<model>`).
- `thread_safe_tools` lists tools that are safe to call in parallel across the ensemble. Anything not in this list is serialized.

You can also pass `available_models_for_ensemble` as a comma-separated string, which is convenient when the value comes from a template variable:

```toml title="config.toml"
AVAILABLE_MODELS = "openai/gpt-4,anthropic/claude-opus-4-7"

[agent_ensemble]
thread_safe_tools = ["google_search", "read_file"]
available_models_for_ensemble = "${AVAILABLE_MODELS}"
```

See the [Agent with Ensemble](../examples/agents_with_features/sample_agent_ensemble.md) example for a complete runnable setup.

---

See the [`[agent_ensemble]` field reference](../../reference/configuration/agent-ensemble.md) for all fields.
