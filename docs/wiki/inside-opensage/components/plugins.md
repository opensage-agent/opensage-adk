# Plugins

A **plugin** is a small, self-contained unit of behavior that hooks into the agent's lifecycle at well-known extension points: after a tool call, after an event is yielded, before a model request. Plugins are how OpenSage ships cross-cutting features like **history summarization**, **tool-response summarization**, **quota tracking**, **doom-loop detection**, **build verification**, and **memory observation** without baking them into every agent.

Source lives under [`src/opensage/plugins/`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/plugins).

## Two Plugin Flavors

OpenSage supports two plugin shapes:

| Flavor | Where | Format | What it can do |
|---|---|---|---|
| **ADK plugins** | `plugins/default/adk_plugins/` | Python `.py`; subclass `BasePlugin` | Override any ADK callback (`after_tool_callback`, `on_event_callback`, `before_model_callback`, and similar). Full programmatic access to state. |
| **Claude Code hooks** | `plugins/default/claude_code_hooks/` | JSON `.json` | Declarative matchers on tool name/arguments. Action types: `prompt` (inject text) or `command` (run in sandbox). |

Claude Code hooks use the same JSON format as [Claude Code hooks](https://docs.anthropic.com/en/docs/claude-code/hooks) and [Gemini CLI hooks](https://github.com/google-gemini/gemini-cli), so existing hook JSON is portable. ADK plugins are the escape hatch when the JSON matcher is not expressive enough.

## Discovery Order

When a session starts, the runtime does:

```python
root_agent = mk_agent(session_id=session_id)

plugins = load_plugins(
    enabled_plugins,                     # names listed in [plugins] enabled
    agent_dir=agent_dir,
    adk_plugin_params=session.config.plugins.adk_plugin_params,
    extra_plugin_dirs=session.config.plugins.extra_plugin_dirs,
)
```

Plugin files are discovered from four locations, in priority order (later sources shadow earlier ones of the same name):

| Priority | Source | Format |
|----------|--------|--------|
| 1 | `src/opensage/plugins/default/adk_plugins/` | Python `.py` |
| 2 | `src/opensage/plugins/default/claude_code_hooks/` | JSON `.json` |
| 3 | `extra_plugin_dirs` (from `[plugins]`) | `.py` or `.json` |
| 4 | `{agent_dir}/plugins/` | `.py` or `.json` |

`enabled = [...]` in `[plugins]` is the allow-list. Plugins not on the list are ignored even if their file exists.

## Built-In Plugins

The ones the OpenSage team actually ships in `src/opensage/plugins/default/adk_plugins/`:

| Plugin | Purpose |
|---|---|
| `history_summarizer_plugin` | Compacts the event log once it grows past `max_history_summary_length`. See [History](./history.md). |
| `tool_response_summarizer_plugin` | Truncates/summarizes any single tool response longer than `max_tool_response_length`. See [History](./history.md). |
| `quota_after_tool_plugin` | Injects `_quota_info = {used, remaining, limit}` after each tool call so the agent can pace itself against `max_llm_calls`. |
| `doom_loop_detector_plugin` | Detects repetitive call patterns and nudges the agent to break out. |
| `build_verifier_plugin` | Verifies that the project still builds after the agent edits files. |
| `memory_observer_plugin` | Writes each event to long-term memory so future sessions can recall via `search_memory`. |
| `image_injection_plugin` | Makes tool outputs that contain image bytes visible to multimodal models. |
| `read_before_edit_plugin` | Gates `str_replace_edit` on `view_file` first, to avoid blind edits. |
| `message_board_diff_plugin` | Surfaces new board messages during ensemble runs via `_message_board_diff` on tool responses. |

None are active by default. Enable them by name in `config.toml`:

```toml title="config.toml"
[plugins]
enabled = [
    "doom_loop_detector_plugin",
    "history_summarizer_plugin",
    "tool_response_summarizer_plugin",
    "quota_after_tool_plugin",
]

[plugins.adk_plugin_params.doom_loop_detector_plugin]
threshold = 5
```

## Execution Semantics

Plugins registered for the same callback run **sequentially in discovery order**. Each plugin can mutate the shared state (tool response dict, event object, message list) before the next one runs. This is intentional: `tool_response_summarizer_plugin` runs first and shortens a big response; then `history_summarizer_plugin` sees the shortened version when it decides whether to compact; then `quota_after_tool_plugin` appends quota info to whatever's left.

The order matters enough that you should think of the plugin stack as a pipeline, not a set of independent observers.

## Related References

- [`[plugins]` configuration reference](../../reference/configuration/plugins.md): field types.
- [Plugins (authoring)](../../developer-guide/adding-plugins.md): authoring new plugins, the `BasePlugin` API, how JSON hooks are loaded.
- [History](./history.md): the summarization plugins in depth.
