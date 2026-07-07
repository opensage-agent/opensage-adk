# History

Long agent runs accumulate tool outputs and event logs. Left alone, they overflow the context window and the model starts dropping information mid-task. OpenSage-ADK keeps runs tractable with two levers, one per tool response and one over the whole history. Both are plugins, and both are off by default.

Source:

- Summarizer engine: [`src/opensage/features/summarization.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/features/summarization.py)
- Plugin hooks: [`history_summarizer_plugin.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/plugins/default/adk_plugins/history_summarizer_plugin.py), [`tool_response_summarizer_plugin.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/plugins/default/adk_plugins/tool_response_summarizer_plugin.py), [`quota_after_tool_plugin.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/plugins/default/adk_plugins/quota_after_tool_plugin.py)

## Two Levers: Truncation and Compaction

| Lever | Scope | Trigger | Plugin |
|---|---|---|---|
| Truncation | One tool response | `max_tool_response_length` (chars, default `10000`) | `tool_response_summarizer_plugin` |
| Compaction | The whole event log | `max_history_summary_length` (chars) | `history_summarizer_plugin` |

Truncation runs first, so a large tool output is shortened before it counts against the total-history budget. One oversized response therefore cannot by itself trigger a full-history compaction.

### Truncation of a Single Tool Response

When a tool returns, the tool-response summarizer checks the length. If the output exceeds `max_tool_response_length`, the plugin either summarizes it through the `summarize` model profile or truncates it to a preview plus a file pointer, depending on how the tool declares its handling. The full output persists to the session's tool-outputs directory inside the sandbox (`get_current_session_tool_outputs_dir`), so the agent can re-read it with `view_file` when a summary drops something load-bearing.

### Compaction of the Whole Event Log

`history_compaction_on_event` in `features/summarization.py` runs from the plugin's `on_event_callback` after each event is appended. It sums the character counts of the folded events, and once the total exceeds `max_history_summary_length`, `OpenSageFullEventSummarizer` runs:

1. Find the last compaction boundary.
2. From the events after that boundary, take the first `compaction_percent` (default `50`) as the compaction window.
3. Expand the window forward to the next paired function-call and function-response boundary, so a tool call never splits from its result.
4. Ask the model to summarize that window, with recent context and quota warnings in the prompt.
5. Replace the window with a single `EventCompaction`. The agent keeps the summary, and the original events remain on disk in `traj.json` for debugging.

The summarizer folds any earlier compaction text into the new summary, so only one compaction survives in the live history. Windows of two events or fewer are skipped. Because compaction runs in `on_event_callback`, it edits `session.events` before ADK's `_ContentLlmRequestProcessor` builds `llm_request.contents`, so the next model call sees already-compacted history with no timing gap.

## Plugin Hooks

The tool-response summarizer, inbox delivery, and quota plugins attach to `after_tool_callback`. History compaction attaches to `on_event_callback`:

```
after_tool_callback pipeline:
  tool_response_summarizer_plugin   # truncate or summarize one response
  inbox_delivery_plugin             # deliver pending inbox messages
  quota_after_tool_plugin           # append _quota_info dict

on_event_callback pipeline:
  history_summarizer_plugin         # compact the whole log if over budget
```

Each `after_tool_callback` plugin mutates the same response dict before the next one reads it. By the time `on_event_callback` fires, the event already sits in `session.events`, so the budget check sees the true context size.

## Quota Countdown

With `enable_quota_countdown` set in `[history]`, `quota_after_tool_plugin` reads the invocation's `max_llm_calls` and attaches `{used, remaining, limit}` to every dict-shaped tool response as `_quota_info`. The tool-response summarizer adds a readable line ("You have X LLM calls remaining") to long-output truncation messages, and the compaction summarizer receives the same figures so its summary can tell the agent to finish soon. The goal is self-pacing: an agent that knows it has eight calls left behaves differently from one that does not.

## Short-Term and Long-Term Memory

History is the agent's short-term memory, the events it saw this session. OpenSage persists short-term memory to files. Each invocation writes a `traj.json` through `persist_traj_json_for_invocation` into the agent's memory directory inside the sandbox (`/mem/short_term/<agent>__<session>/`), and mirrors it to the host under `~/.local/opensage/sessions/` so the web UI can replay the run. The code lives in `src/opensage/memory/file_based/short_term/session_files.py`.

Long-term memory is also file-based. `ensure_long_term_knowledge_store` seeds an `index.md` under the memory root (`/mem/long_term/index.md`), which the agent reads and updates with ordinary file tools to carry knowledge across sessions. The code lives in `src/opensage/memory/file_based/long_term/knowledge_store.py`. An earlier database-backed memory backend has been removed; memory is file-based only.

## Tuning Guide

| Symptom | Likely lever |
|---|---|
| One tool returned 50k chars and the model got confused | Lower `max_tool_response_length`. |
| Agent forgets what it did 30 calls ago | Raise `max_history_summary_length` if context budget allows; otherwise the run is already compacting and the summary is lossy. |
| Summaries drop critical context | Lower `compaction_percent` from 50 to 30, so each pass compacts a smaller window. |
| Agent does not pace itself on long runs | Set `enable_quota_countdown`. |

## Related References

- [`[history]` configuration reference](../../reference/configuration/history.md): field types and defaults.
- [Customize: Tune Memory and History](../../get-started/customize/history.md): the how-to.
- [Plugins](./plugins.md): the plugin pipeline in general.
