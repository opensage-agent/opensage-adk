# Plugins

**Section:** `[plugins]`

Plugins are small opt-in behaviors that hook into the agent's lifecycle: doom-loop detection, tool-response summarization, post-tool quota enforcement, and more. Enable the ones you want by name; OpenSage discovers them from its built-in directory and from `~/.local/opensage/plugins/` automatically.

## Turning Plugins On

```toml title="config.toml"
[plugins]
enabled = [
    "doom_loop_detector_plugin",
    "history_summarizer_plugin",
    "tool_response_summarizer_plugin",
    "quota_after_tool_plugin",
]
```

## Passing kwargs to a Plugin

Some plugins accept configuration. Pass kwargs by name under `[plugins.adk_plugin_params.<plugin_name>]`:

```toml title="config.toml"
[plugins.adk_plugin_params.doom_loop_detector_plugin]
threshold = 5
```

## Adding Your Own Plugins

Drop your `.py` plugin into `~/.local/opensage/plugins/` to make it discoverable by every agent, or list an extra directory in `extra_plugin_dirs`:

```toml title="config.toml"
[plugins]
enabled = ["my_custom_plugin"]
extra_plugin_dirs = ["/path/to/shared/plugins"]
```

See [Plugins (concept)](../../inside-opensage/components/plugins.md) for how plugins are structured and [Adding Plugins](../../developer-guide/adding-plugins.md) for authoring a new one.

---

See the [`[plugins]` field reference](../../reference/configuration/plugins.md) for all fields and discovery paths.
