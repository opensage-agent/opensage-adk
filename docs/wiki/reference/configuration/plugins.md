# `[plugins]` Reference

Field reference for the `[plugins]` section. For how to use it, see the [Plugins configuration guide](../../get-started/customize/plugins.md).

## Fields

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `enabled` | `list[string]` | List of enabled plugin names (or regex patterns) | `[]` |
| `extra_plugin_dirs` | `list[string]` | Additional directories to search for plugins | `[]` |
| `adk_plugin_params` | `dict[string, dict]` | Per-ADK-plugin constructor kwargs, keyed by plugin name | `{}` |

## Default Plugin Discovery Paths

No extra config is required; these directories are always searched:

| Path | Contents |
|------|----------|
| `src/opensage/plugins/default/adk_plugins/` | Built-in ADK plugins |
| `src/opensage/plugins/default/claude_code_hooks/` | Built-in Claude hook plugins |
| `~/.local/opensage/plugins/` | User-local plugins (`.py` and `.json`) |

Use `extra_plugin_dirs` to add more directories.
