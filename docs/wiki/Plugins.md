# Plugins

## Overview

OpenSage uses a plugin system built on top of the ADK (Agent Development Kit) `BasePlugin` interface. Plugins hook into
the agent's tool execution lifecycle — they can run logic **before** or **after** any tool call, injecting prompts,
running sandbox commands, or modifying tool results.

There are two kinds of plugins:

1. **ADK plugins** (`.py`) — Python classes that subclass `google.adk.plugins.base_plugin.BasePlugin` with full
   programmatic control
2. **Claude Code hooks** (`.json`) — Declarative JSON rules using the Claude Code / Gemini CLI hook format, bridged into
   the ADK plugin system.

Both kinds are loaded and participate in the same callback lifecycle.

## Quick Start

**No plugins are enabled by default.** Explicitly list the plugins you need in your agent's TOML config. Execution order
matches the list order.

```toml
[plugins]
enabled = [
    "doom_loop_detector_plugin", # ADK plugin (Python)
    "read_before_edit_plugin", # ADK plugin (Python)
    "careful_edit", # Claude Code hook (JSON)
    "test_output_review", # Claude Code hook (JSON)
]
```

### Recommended Presets

These plugins are lightweight, have no external dependencies, and are safe to enable for any agent or benchmark:

```toml
[plugins]
enabled = [
    "doom_loop_detector_plugin", # Breaks out of repeated identical tool calls
    "history_summarizer_plugin", # Compacts conversation history when it exceeds budget
    "tool_response_summarizer_plugin", # Summarizes verbose tool output via LLM
    "quota_after_tool_plugin", # Shows remaining LLM call budget after each tool use
]
```

## Available Plugins

**ADK plugins** (`default/adk_plugins/`):

| Plugin                            | Callback       | Description                                                                                   |
|-----------------------------------|----------------|-----------------------------------------------------------------------------------------------|
| `doom_loop_detector_plugin`       | before_tool    | Blocks consecutive identical tool calls at threshold (default: 3)                             |
| `read_before_edit_plugin`         | before + after | Warns when agent edits a file it hasn't read first                                            |
| `build_verifier_plugin`           | after_tool     | Intercepts `finish_task` and runs build verification; blocks completion if build fails        |
| `tool_response_summarizer_plugin` | after_tool     | Summarizes tool responses exceeding `max_tool_response_length` via LLM. **Modifies `result`** |
| `history_summarizer_plugin`       | after_tool     | Compacts conversation history when it exceeds budget                                          |
| `memory_observer_plugin`          | after_tool     | Persists valuable tool results to Neo4j (async). Requires `[memory] enabled = true`           |
| `quota_after_tool_plugin`         | after_tool     | Appends remaining LLM call quota to tool responses                                            |
| `message_board_diff_plugin`       | after_tool     | Appends unread message board diffs in ensemble runs                                           |
| `parts_from_tool`                 | after_tool     | Injects image/multimodal `types.Part` from tool results into next LLM request                 |

**Claude Code hooks** (`default/claude_code_hooks/`):

| Hook                  | Callback       | Description                                                                                                 |
|-----------------------|----------------|-------------------------------------------------------------------------------------------------------------|
| `careful_edit`        | before + after | Pre/post guard on `str_replace_editor` — reminds agent to verify content before editing, re-read on failure |
| `test_output_review`  | after_tool     | Post-hook on test commands (pytest, go test, cargo test, etc.) — prompts careful analysis of failures       |
| `verify_after_change` | after_tool     | Post-hook on `git diff` and `patch` — prompts review of unintended changes and patch failures               |

## Execution Order

**Execution order matches the `enabled` list exactly.** Both ADK plugins and Claude Code hooks are each loaded as
independent plugin instances in the order they appear. You can interleave them to control callback priority:

```toml
enabled = [
    "careful_edit", # CC hook — runs first
    "doom_loop_detector_plugin", # ADK plugin — runs second
    "test_output_review", # CC hook — runs third
]
```

### Why Order Matters

Multiple plugins sharing the same callback stage (`after_tool_callback`) execute sequentially. If one plugin **modifies
** `result["output"]`, later plugins see the modified version. Example:

```toml
# Order A: CC hook appends prompt, then summarizer truncates everything
enabled = ["test_output_review", "tool_response_summarizer_plugin"]
# → Summarizer sees: original output + "[Plugin] Analyze test failures..."
# → Summarizer may truncate the hook's prompt along with the output

# Order B: Summarizer truncates first, then CC hook appends prompt
enabled = ["tool_response_summarizer_plugin", "test_output_review"]
# → Hook appends to already-truncated output
# → Model sees: truncated output + "[Plugin] Analyze test failures..." (prompt preserved)
```

Similarly, `doom_loop_detector_plugin` can **block tool execution entirely** in `before_tool_callback`. If it runs
before other before_tool plugins, those plugins never see the call; if it runs after, earlier plugins still execute
before the block kicks in.

## User-Defined Plugins

**Agent-local plugins** — place under `{agent_dir}/plugins/`:

```
examples/agents/my_agent/
├── agent.py
└── plugins/
    ├── my_custom_plugin.py     # ADK plugin
    └── my_hooks.json           # Claude Code hooks
```

```toml
[plugins]
enabled = ["my_custom_plugin", "my_hooks"]
```

**Shared plugins across agents** — use `extra_plugin_dirs` to add custom search directories:

```toml
[plugins]
enabled = ["shared_linter_plugin", "my_custom_plugin"]
extra_plugin_dirs = ["/path/to/shared/plugins"]
```

Directories listed in `extra_plugin_dirs` are searched after the framework defaults but before `{agent_dir}/plugins/`,
so agent-local plugins can shadow shared ones. Both `.py` and `.json` files are discovered from these directories.

## Writing Plugins

### ADK Plugins (Python)

Subclass `google.adk.plugins.base_plugin.BasePlugin` and override any of its callback methods. The file must contain
exactly one `BasePlugin` subclass; the filename (without `.py`) is the plugin name used in `enabled`. See existing
plugins in `default/adk_plugins/` for examples.

Available callbacks (see [ADK BasePlugin](https://google.github.io/adk-docs/callbacks/) for full details):

| Callback                                         | When                                 |
|--------------------------------------------------|--------------------------------------|
| `before_tool_callback` / `after_tool_callback`   | Before / after a tool call           |
| `on_tool_error_callback`                         | When a tool call raises an exception |
| `before_model_callback` / `after_model_callback` | Before / after an LLM request        |
| `on_model_error_callback`                        | When an LLM call raises an exception |
| `before_agent_callback` / `after_agent_callback` | Before / after agent execution       |
| `before_run_callback` / `after_run_callback`     | Before / after the Runner invocation |
| `on_user_message_callback`                       | When a user message is received      |
| `on_event_callback`                              | After an event is yielded            |

### Claude Code Hooks (JSON)

Declarative JSON rules that match tool calls by name/argument pattern and inject prompts or run sandbox commands. The
JSON format is fully compatible with [Claude Code hooks](https://docs.anthropic.com/en/docs/claude-code/hooks)
and [Gemini CLI hooks](https://github.com/google-gemini/gemini-cli). Supported events: `PreToolUse` (`BeforeTool`),
`PostToolUse` (`AfterTool`). Matchers: exact name, pipe-separated (`"bash|read_file"`), argument glob (
`"bash(git commit*)"`), or wildcard (`"*"`). Action types: `prompt` (inject text) or `command` (run in sandbox). See
existing hooks in `default/claude_code_hooks/` for examples.

## How Plugin Works

`load_plugins(enabled_plugins, agent_dir)` resolves each name in the `enabled_plugins` list:

1. **Literal plugin name** — looked up by exact name in the discovered plugin registry. **Raises `ValueError`** if not
   found (likely a typo or missing file).
2. **Regex pattern** (auto-detected by metacharacters, e.g. `".*_plugin"`) — matched via `re.fullmatch` against all
   discovered plugin names. If nothing matches, a **warning** is logged but loading continues (the pattern is treated
   as "load whatever matches, if any").

### Discovery Priority

Plugins are discovered from the following directories. When the same plugin name appears in multiple directories, the
later directory **shadows** (overrides) the earlier one:

| Priority    | Directory                            | Contents                        | Shadowed by |
|-------------|--------------------------------------|---------------------------------|-------------|
| 1 (lowest)  | `plugins/default/adk_plugins/`       | Framework default `.py` plugins | 3, 4        |
| 2           | `plugins/default/claude_code_hooks/` | Framework default `.json` hooks | 3, 4        |
| 3           | `extra_plugin_dirs` entries          | User-shared `.py` and `.json`   | 4           |
| 4 (highest) | `{agent_dir}/plugins/`               | Agent-local `.py` and `.json`   | —           |

For example, if both the framework and your agent directory contain `doom_loop_detector_plugin.py`, the agent-local
version takes precedence. Similarly, a plugin in `extra_plugin_dirs` overrides the framework default but is itself
overridden by an agent-local plugin with the same name.

### Directory Structure

```
src/opensage/plugins/
├── __init__.py                    # load_plugins() entry point
├── adk_plugin_loader.py           # Loads .py plugin classes
├── claude_code_hook_loader.py     # Loads .json hook declarations
└── default/
    ├── adk_plugins/               # Default Python plugins
    │   ├── build_verifier_plugin.py
    │   ├── doom_loop_detector_plugin.py
    │   ├── read_before_edit_plugin.py
    │   ├── history_summarizer_plugin.py
    │   ├── memory_observer_plugin.py
    │   ├── message_board_diff_plugin.py
    │   ├── quota_after_tool_plugin.py
    │   ├── tool_response_summarizer_plugin.py
    │   └── parts_from_tool.py
    └── claude_code_hooks/          # Default JSON hooks
        ├── careful_edit.json
        ├── test_output_review.json
        └── verify_after_change.json
```

## See Also

- [Configuration](Configuration.md#plugins-configuration) — Plugin configuration reference
- [Adding Tools](Adding-Tools.md) — How to create tools (distinct from plugins)
- [Core Components](Core-Components.md) — System architecture overview
