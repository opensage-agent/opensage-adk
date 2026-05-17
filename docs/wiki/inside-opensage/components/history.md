# History

Long agent runs accumulate tool outputs and event logs. Left alone, they overflow the context window and the model starts dropping information mid-task. OpenSage-ADK keeps runs tractable with two coordinated levers (one per-tool-response, one whole-history), both implemented as plugins and off by default.

Source:

- Summarizer engine: [`src/opensage/features/summarization.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/features/summarization.py)
- Plugin hooks: [`src/opensage/plugins/default/adk_plugins/history_summarizer_plugin.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/plugins/default/adk_plugins/history_summarizer_plugin.py), [`tool_response_summarizer_plugin.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/plugins/default/adk_plugins/tool_response_summarizer_plugin.py), [`quota_after_tool_plugin.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/plugins/default/adk_plugins/quota_after_tool_plugin.py)

## Two Levers: Truncation vs Compaction

| Lever | Scope | Trigger | Plugin |
|---|---|---|---|
| **Truncation** | One tool response | `max_tool_response_length` (chars, default `10000`) | `tool_response_summarizer_plugin` |
| **Compaction** | The whole event log | `max_history_summary_length` (chars, default `100000`) | `history_summarizer_plugin` |

They run in that order: a big tool output is shortened *before* it is measured against the total-history budget. The net effect is that one oversized response cannot single-handedly trigger a full-history compaction.

### Truncation (Per Tool Response)

When a tool returns, `tool_response_summarizer_callback` checks its length. If it exceeds `max_tool_response_length`, the plugin either:

1. **Summarizes** the output via the `summarize` model profile, or
2. **Truncates** it to a preview + a file pointer, depending on the tool's declared handling.

The full output is always persisted to `/workspace/.tool_outputs/<id>` inside the sandbox so the agent can go re-read it deliberately via `view_file` if the summary drops something load-bearing.

### Compaction (Whole-Event-Log)

`history_compaction_before_model` (in `features/summarization.py`) sums the character counts of all folded events before each LLM call. If the total exceeds `max_history_summary_length`, `OpenSageFullEventSummarizer` kicks in:

1. Find the last compaction boundary.
2. From events *after* that boundary, take the first `compaction_percent` (default `50`) as the compaction window.
3. Expand the window forward to the next **paired function-call / function-response** boundary; never split a tool call from its result.
4. Ask the LLM to summarize that window, with recent context and quota warnings injected into the prompt.
5. Replace the window with a single `Event` of type `EventCompaction`. The agent keeps the summary; the originals are still on disk / in the Neo4j session log for debugging.

Windows of ≤2 events are skipped: not worth the LLM round-trip.

## Plugin Hooks

Both summarizers attach to **`after_tool_callback`**, the ADK lifecycle hook that fires right after a tool returns. Registration order matters:

```
after_tool_callback pipeline:
  tool_response_summarizer_plugin   # truncate single response
  history_summarizer_plugin         # compact whole log if over budget
  quota_after_tool_plugin           # append _quota_info dict
```

Each plugin mutates the same response dict before the next sees it. By the time the LLM is next called, the response it is looking at may have been summarized, the history behind it may have been compacted, and an `_quota_info` field may have been appended.

## Quota Countdown

When `enable_quota_countdown = true` in `[history]`, `quota_after_tool_plugin` reads the invocation's `max_llm_calls` and attaches `{used, remaining, limit}` to every dict-shaped tool response as `_quota_info`. The `tool_response_summarizer` also injects a human-readable line ("You have X LLM calls remaining") into long-output truncation messages, and the compaction summarizer is passed the same info so its summary can flag "finish soon" explicitly.

The point is to let the model *pace itself*. An agent that knows it has 8 calls left behaves differently from one that does not.

## Short-Term vs Long-Term Memory

History is the agent's **short-term memory**: the events it saw this session. OpenSage has two short-term backends:

- **File-based (default).** Each invocation is persisted via `persist_traj_json_for_invocation()` to `/mem/short_term/<agent>__<session>/` inside the sandbox and mirrored to `~/.local/opensage/sessions/` on the host (so the web UI can replay it). Code: `src/opensage/memory/file_based/short_term/session_files.py`.
- **Database-backed.** When `memory.database.short_term.enabled = true`, events and tool responses are written to Neo4j via [`src/opensage/memory/database_based/short_term/history_store.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/memory/database_based/short_term/history_store.py). Compaction summaries are stored as linked nodes so you can later query *"what did the agent summarize, and from which raw events?"*

Long-term memory is a separate subsystem (`src/opensage/memory/database_based/long_term/`). Tools like `search_memory` hit that store to recall patterns learned across previous sessions (not covered on this page). See the [SWE-Bench Pro agent](../examples/swebenchpro_agent.md) for a working example.

## Tuning Guide

| Symptom | Likely lever |
|---|---|
| One tool returned 50k chars and the model got confused | Lower `max_tool_response_length`. |
| Agent forgets what it did 30 calls ago | Raise `max_history_summary_length` if you have context budget; otherwise it is already compacting and the summary is lossy; accept it or switch to DB-backed memory. |
| Summaries drop critical context | Drop `compaction_percent` from 50 -> 30 so less of each window is compacted at a time. |
| Agent does not pace itself on long runs | Enable `enable_quota_countdown`. |

## Neo4j Logging Internals

When the DB-backed backend is active, agent lifecycle events are recorded via a monkey-patch layer in [`src/opensage/patches/neo4j_logging.py`](https://github.com/opensage-agent/opensage-adk/tree/main/src/opensage/patches/neo4j_logging.py). It is applied via `neo4j_logging.apply()` and activated via `neo4j_logging.enable()` before the agent runs. Both `BaseAgent.run_async` and `AgentTool.run_async` are wrapped.

### What the Patch Records

- **Agent start/end**: creates an `AgentRun` node per run, with start time, end time, status (`completed` or `error`), and final output text.
- **Agent input**: the initial user message, captured by `record_agent_start` when the run begins.
- **Agent output**: the last event's text content, captured by `record_agent_end` when the run ends.
- **Agent-to-agent calls**: when a parent invokes a sub-agent via `AgentTool`, an `AGENT_CALLS` relationship links the two `AgentRun` nodes and carries the request content.
- **Every streamed event**: `log_single_event_neo4j` writes each event (tool call, model response, compaction marker) as an `Event` node and links it to the `AgentRun` via `HAS_EVENT`.
- **Final session state**: persisted on the `AgentRun` node at completion.

### Plugin-Side Writes

The history plugins introduced above also write to Neo4j when DB-backed memory is enabled:

| Relationship / Node | Written by | Purpose |
|---|---|---|
| `RawToolResponse` node | `tool_response_summarizer_plugin` | Full raw response saved when a tool output exceeds `max_tool_response_length` (default 10000 chars), so the in-context summary can be compared against the original. |
| `AGENT_RUN_HAS_RAW_TOOL_RESPONSE` | `tool_response_summarizer_plugin` | Links the `AgentRun` to each `RawToolResponse` it produced. |
| `SUMMARIZES_EVENTS` | `history_summarizer_plugin` | Links the compaction summary `Event` (type `history_summary`) to the original events it replaced, preserving lineage. |

These relationships make it possible to reconstruct what the agent actually saw at each step, even after truncation or compaction hid the raw content from the live context window.

## Related References

- [`[history]` configuration reference](../../reference/configuration/history.md): field types and defaults.
- [Customize -> Tune Memory & History](../../get-started/customize/history.md): the how-to.
- [Plugins](./plugins.md): the plugin pipeline in general.
