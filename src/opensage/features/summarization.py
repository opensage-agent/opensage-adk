import asyncio
import json
import logging
import math
from datetime import datetime
from typing import List, Optional, Set

from google.adk.agents.base_agent import BaseAgent
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions, EventCompaction
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from opensage.features.agent_history_tracker import is_neo4j_logging_enabled
from opensage.utils.agent_utils import (
    discover_all_agents,
    get_opensage_session_id_from_context,
    register_callback_to_all_agents,
    resolve_model_spec,
    save_content_to_sandbox_file,
)

logger = logging.getLogger(__name__)


def _estimate_event_chars(event: Event) -> int:
    """Estimate character length of an event's content for budgeting."""
    import json

    total_chars = 0
    if event.content and event.content.parts:
        for part in event.content.parts:
            if part.text:
                total_chars += len(part.text)
            elif part.function_call:
                total_chars += len(
                    json.dumps(
                        {
                            "name": part.function_call.name,
                            "args": part.function_call.args,
                        }
                    )
                )
            elif part.function_response:
                total_chars += len(
                    json.dumps(
                        {
                            "name": part.function_response.name,
                            "response": part.function_response.response,
                        }
                    )
                )
    return total_chars


def _render_event_parts_for_context(event: Event) -> List[str]:
    """Render event text/tool call/response parts for prompt context."""
    lines: List[str] = []
    if not getattr(event, "content", None) or not event.content.parts:
        return lines
    for part in event.content.parts:
        if getattr(part, "text", None):
            lines.append(part.text)
        elif getattr(part, "function_call", None):
            fc = part.function_call
            name = getattr(fc, "name", "unknown_tool")
            args = getattr(fc, "args", {})
            try:
                args_str = json.dumps(args, ensure_ascii=False)
            except Exception:
                args_str = str(args)
            lines.append(f"[ToolCall] {name}({args_str})")
        elif getattr(part, "function_response", None):
            fr = part.function_response
            name = getattr(fr, "name", "unknown_tool")
            resp = getattr(fr, "response", {})
            try:
                resp_str = json.dumps(resp, ensure_ascii=False)
            except Exception:
                resp_str = str(resp)
            lines.append(f"[ToolResponse] {name} -> {resp_str}")
    return lines


def _group_invocation_rounds(events: List[Event], branch: Optional[str]) -> List[str]:
    """Return ordered unique invocation_ids for the given branch."""
    seen: Set[str] = set()
    ordered: List[str] = []
    for ev in events:
        if branch and ev.branch and ev.branch != branch:
            continue
        inv = getattr(ev, "invocation_id", None)
        if not inv:
            continue
        if inv not in seen:
            seen.add(inv)
            ordered.append(inv)
    return ordered


async def _get_summary_async(model, llm_request):
    """Get summary from model asynchronously."""
    summary_parts = []
    async for llm_response in model.generate_content_async(llm_request):
        if llm_response.content and llm_response.content.parts:
            for part in llm_response.content.parts:
                if getattr(part, "text", None):
                    summary_parts.append(part.text)
    return "".join(summary_parts).strip()


async def tool_response_summarizer_callback(tool, args, tool_context, tool_response):
    """
    Summarize long tool responses, save full output to file, and optionally persist to Neo4j.
    Mutates dict tool responses in-place and returns None to avoid short-circuiting
    other plugins/callbacks.
    """
    from opensage.session import get_opensage_session

    opensage_session_id = get_opensage_session_id_from_context(tool_context)
    opensage_session = get_opensage_session(opensage_session_id)

    max_len = getattr(
        getattr(opensage_session.config, "history", None),
        "max_tool_response_length",
        10000,
    )
    if not isinstance(tool_response, dict):
        return None

    raw = str(tool_response)
    tool_name = getattr(tool, "name", "unknown_tool")

    if len(raw) < int(max_len):
        logger.debug(
            f"[ToolResponseSummarizer] Tool '{tool_name}' response below threshold "
            f"({len(raw)} < {max_len}), skipping"
        )
        return None

    logger.warning(
        f"[ToolResponseSummarizer] Processing tool '{tool_name}':\n"
        f"  response_length: {len(raw)} chars\n"
        f"  max_len: {max_len}\n"
        f"  session_id: {opensage_session_id}"
    )

    # Save full output to file in sandbox using shared utility
    output_dir = "/workspace/.tool_outputs"

    logger.warning(
        f"[ToolResponseSummarizer] Saving full output to file for '{tool_name}'"
    )
    output_file = save_content_to_sandbox_file(
        context=tool_context,
        content=raw,
        tool_name=tool_name,
        output_dir=output_dir,
    )
    file_saved = output_file is not None
    logger.warning(
        f"[ToolResponseSummarizer] File save result for '{tool_name}': "
        f"{'SUCCESS' if file_saved else 'FAILED'}, file={output_file}"
    )

    # For very long responses (>50000 chars), skip summarization and just truncate
    # Summarization may fail or be unreliable for extremely long content
    SKIP_SUMMARY_THRESHOLD = 50000
    if len(raw) > SKIP_SUMMARY_THRESHOLD:
        logger.info(
            f"Tool response too long ({len(raw)} chars > {SKIP_SUMMARY_THRESHOLD}), "
            "skipping summarization and using truncation instead"
        )
        logger.info(
            f"Truncation path: file_saved={file_saved}, output_file={output_file}"
        )
        PREVIEW_CHARS = 200
        truncated_preview = raw[:PREVIEW_CHARS]
        logger.info(f"Created truncated preview: {len(truncated_preview)} chars")

        if file_saved and output_file:
            truncated_msg = f"""<Summary by opensage>
The tool response is too long ({len(raw):,} characters) to include here.
Here is a brief preview:

{truncated_preview}

[Full Output Saved]
The complete output has been saved to: {output_file}
You MAY use `grep`, `cat`, `head`, `tail` or other commands to search or view the full content.
</Summary by opensage>"""
        else:
            truncated_msg = f"""<Summary by opensage>
The tool response is too long ({len(raw):,} characters) to include here.
Here is a brief preview:

{truncated_preview}

[Warning] Failed to save full output to file. You may need to find other ways to access the full content.
</Summary by opensage>"""

        # Add quota info
        try:
            enable_quota = bool(
                getattr(
                    getattr(opensage_session.config, "history", None),
                    "enable_quota_countdown",
                    True,
                )
            )
        except Exception:
            enable_quota = False
        if enable_quota:
            inv_ctx = tool_context._invocation_context
            try:
                limit = int(
                    getattr(getattr(inv_ctx, "run_config", None), "max_llm_calls", 0)
                    or 0
                )
            except Exception:
                limit = 0
            try:
                used = int(
                    getattr(
                        getattr(inv_ctx, "_invocation_cost_manager", None),
                        "_number_of_llm_calls",
                        0,
                    )
                    or 0
                )
            except Exception:
                used = 0
            if limit > 0:
                remaining = max(0, limit - used)
                truncated_msg += f"\n[Quota] You have {remaining} LLM calls remaining"
            else:
                truncated_msg += "\n[Quota] LLM calls: unlimited"

        logger.info(
            f"Returning truncated message (skipped summarization): "
            f"original_len={len(raw)}, truncated_msg_len={len(truncated_msg)}"
        )
        tool_response.clear()
        tool_response["_tool_response_summarized"] = True
        tool_response["_tool_response_summary"] = truncated_msg
        if file_saved and output_file:
            tool_response["_tool_response_file"] = output_file
        tool_response["result"] = truncated_msg
        return None

    model_name = getattr(opensage_session.config.llm, "summarize_model", None)
    agent = tool_context._invocation_context.agent
    if model_name:
        model = resolve_model_spec(model_name, tool_context=tool_context)
    else:
        if not hasattr(agent, "canonical_model"):
            logger.warning("Agent has no model, skipping tool response summarization")
            return None
        model = agent.canonical_model

    llm_request = LlmRequest()
    llm_request.model = getattr(
        model, "model", None
    )  # Set model name from the model object
    llm_request.config = types.GenerateContentConfig()

    # Build recent context window (up to last 10 events).
    recent_context_lines: List[str] = []
    try:
        session = getattr(tool_context._invocation_context, "session", None)
        events: List[Event] = list(getattr(session, "events", []) or [])
    except Exception:
        events = []
    event_blocks: List[str] = []
    block_lengths: List[int] = []
    if events:
        for event in events[-10:]:
            rendered_parts = _render_event_parts_for_context(event)
            if not rendered_parts:
                continue
            author = getattr(event, "author", "unknown")
            timestamp = getattr(event, "timestamp", None)
            header = f"{author}" if timestamp is None else f"{author} @ {timestamp}"
            block_lines = [f"{header}:"] + [f"  {line}" for line in rendered_parts]
            block = "\n".join(block_lines)
            event_blocks.append(block)
            block_lengths.append(len(block) + 1)  # include newline between blocks
    if event_blocks:
        max_context_len = 50000
        total_len = sum(block_lengths)
        while event_blocks and total_len > max_context_len:
            removed_len = block_lengths.pop(0)
            event_blocks.pop(0)
            total_len -= removed_len
        context_block = "\n".join(event_blocks)
    else:
        context_block = "<no recent context captured>"

    summary_prompt = (
        "Please summarize the following tool execution concisely.\n\n"
        f"Recent context (last {min(len(events), 10)} events):\n{context_block}\n\n"
        f"Tool: {getattr(tool, 'name', 'unknown')}\n"
        f"Arguments: {args}\n"
        f"Response: {raw[:70000]}{'...' if len(raw) > 70000 else ''}\n\n"
        "Instructions:\n"
        "1. Use the recent context to infer the most likely intent/purpose of this tool call."
        " State it as 'Inferred Intent: ...'.\n"
        "2. Provide a brief 6-9 sentence summary that focuses on details relevant to that intent."
        " Emphasize which parts of the response are critical for the inferred goal.\n"
        "3. Then attach the most critical key information from the Response verbatim.\n\n"
        "Output format:\n"
        "Inferred Intent: ...\n"
        "Summary:\n"
        "- ...\n\n"
        "Key Information (verbatim):\n"
        "- ...\n"
    )
    llm_request.contents = [
        types.Content(role="user", parts=[types.Part.from_text(text=summary_prompt)])
    ]

    try:
        summary = await _get_summary_async(model, llm_request)
    except Exception as e:
        logger.error(f"Error summarizing tool response: {e}")
        summary = raw[:1000] + ("..." if len(raw) > 1000 else "")

    # Build the full summary message
    summary_header = """
    The tool response is too long, so we need to summarize it. We inferred the intent for you to call this tool and kept critical information related to the inferred intent.
    If the inferred intent of calling this tool doesn't match your expectation, you should call the appropriate tool with appropriate arguments to get the details. Here are the inferred intent and the summary:
    """

    # Add file path info if saved successfully
    if file_saved and output_file:
        file_info = f"""

[Full Output Saved]
The complete output has been saved to: {output_file}
You can use `grep`, `cat`, or other commands to search or view the full content if needed.
"""
    else:
        file_info = ""

    summary = summary_header + summary + file_info

    tagged_summary = f"<Summary by opensage>{summary}</Summary by opensage>"

    # Append quota countdown line if enabled
    try:
        enable_quota = bool(
            getattr(
                getattr(opensage_session.config, "history", None),
                "enable_quota_countdown",
                True,
            )
        )
    except Exception:
        enable_quota = False
    if enable_quota:
        inv_ctx = tool_context._invocation_context
        try:
            limit = int(
                getattr(getattr(inv_ctx, "run_config", None), "max_llm_calls", 0) or 0
            )
        except Exception:
            limit = 0
        try:
            used = int(
                getattr(
                    getattr(inv_ctx, "_invocation_cost_manager", None),
                    "_number_of_llm_calls",
                    0,
                )
                or 0
            )
        except Exception:
            used = 0
        if limit > 0:
            remaining = max(0, limit - used)
            tagged_summary += f"\n[Quota] You have {remaining} LLM calls remaining"
        else:
            tagged_summary += "\n[Quota] LLM calls: unlimited"

    if is_neo4j_logging_enabled():
        from opensage.utils.neo4j_history_management import (
            create_raw_tool_response_node,
        )

        await create_raw_tool_response_node(
            tool, args, tool_context, tool_response, tagged_summary
        )
    tool_response.clear()
    tool_response["_tool_response_summarized"] = True
    tool_response["_tool_response_summary"] = tagged_summary
    if file_saved and output_file:
        tool_response["_tool_response_file"] = output_file
    tool_response["result"] = tagged_summary
    return None


class OpenSageFullEventSummarizer:
    """Summarizer including text, tool calls/responses, and previous compaction text."""

    def __init__(self, model: LiteLlm):
        self._model = model

    def _format_event_to_text(self, event: Event) -> List[str]:
        lines: List[str] = []
        if not event.content or not event.content.parts:
            return lines
        author = getattr(event, "author", "unknown")
        for part in event.content.parts:
            if getattr(part, "text", None):
                lines.append(f"{author}: {part.text}")
            elif getattr(part, "function_call", None):
                fc = part.function_call
                name = getattr(fc, "name", "unknown_tool")
                args = getattr(fc, "args", {})
                try:
                    import json as _json

                    args_str = _json.dumps(args, ensure_ascii=False)
                except Exception:
                    args_str = str(args)
                lines.append(f"{author}: [ToolCall] {name}({args_str})")
            elif getattr(part, "function_response", None):
                fr = part.function_response
                name = getattr(fr, "name", "unknown_tool")
                resp = getattr(fr, "response", {})
                try:
                    import json as _json

                    resp_str = _json.dumps(resp, ensure_ascii=False)
                except Exception:
                    resp_str = str(resp)
                lines.append(f"{author}: [ToolResponse] {name} -> {resp_str}")
        return lines

    async def maybe_summarize_events(
        self,
        *,
        events: List[Event],
        folded_context_text: Optional[str] = None,
        quota_info: Optional[dict] = None,
    ) -> Optional[types.Content]:
        if not events:
            return None

        lines: List[str] = []
        # Provide folded full-history as background context
        if folded_context_text:
            lines.append("[Context]")
            lines.append(folded_context_text)
            lines.append("")

        # Add quota warning at the top if available
        if quota_info:
            used = quota_info.get("used", 0)
            limit = quota_info.get("limit", 0)
            remaining = quota_info.get("remaining", 0)
            if limit > 0:
                pct_used = int((used / limit) * 100) if limit > 0 else 0
                lines.append(f"[⚠️ QUOTA WARNING]")
                lines.append(
                    f"LLM calls: {used}/{limit} used ({pct_used}%), {remaining} remaining."
                )
                lines.append(
                    f"The agent MUST prioritize completing the main task over exploration."
                )
                lines.append("")

        # Explicitly mark the window that should be summarized
        lines.append("[WindowToSummarize]")
        for ev in events:
            # Skip compaction events as sources; their summaries already exist downstream
            if getattr(ev, "actions", None) and getattr(ev.actions, "compaction", None):
                continue
            # Add a per-event header to make authorship/timing explicit
            try:
                _ev_author = getattr(ev, "author", "unknown")
                _ev_ts = getattr(ev, "timestamp", None)
                if _ev_ts is not None:
                    lines.append(f"[Event author={_ev_author} ts={_ev_ts}]")
                else:
                    lines.append(f"[Event author={_ev_author}]")
            except Exception:
                # In case of unexpected event shape, still attempt to render parts
                lines.append("[Event]")
            lines.extend(self._format_event_to_text(ev))

        prompt = (
            "You are given background context under [Context] (if present), and a "
            "target window under [WindowToSummarize]. Only summarize the content "
            "under [WindowToSummarize]; do not re-summarize [Context]. First, provide "
            "a very detailed process narrative (more than 10 sentences) that "
            "describes the process in order. Cover: actors/roles, "
            "intents/goals, inputs/outputs, tools used (names and key arguments), "
            "errors/exceptions, intermediate results, decisions made, alternatives "
            "and rationale, do not include timestamps/ids. Then provide a focused "
            "and detailed breakdown with the following sections:\n"
            "1) Key Points: bullet list of the most important facts/decisions.\n"
            "2) Incorrect Attempts: what was tried, why it was wrong/failed.\n"
            "3) Lessons Learned: actionable takeaways to guide next steps.\n"
            "4) Task Status:\n"
            "   - Started: tasks that began in this window.\n"
            "   - Completed: tasks finished in this window.\n"
            "   - Not Completed: tasks still pending or blocked.\n\n" + "\n".join(lines)
        )

        llm_request = LlmRequest()
        llm_request.model = getattr(
            self._model, "model", None
        )  # Set model name from the model object
        llm_request.config = types.GenerateContentConfig()
        llm_request.contents = [
            types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        ]

        try:
            summary_text = await _get_summary_async(self._model, llm_request)
        except Exception as e:
            logger.error(f"Error generating compaction summary: {e}")
            return None

        if not summary_text:
            return None

        return types.Content(
            role="model", parts=[types.Part.from_text(text=summary_text)]
        )


async def history_summarizer_callback(tool, args, tool_context, tool_response):
    """
    Compaction-based history summarization:
    - Decide a stable window (older events) to compact based on thresholds
    - Create a compaction Event via LlmEventSummarizer
    - Append compaction event; do not delete/overwrite original events
    - Neo4j: record summary node and link to the original window
    """
    # Import here to avoid circular import
    from opensage.session import get_opensage_session

    session = tool_context._invocation_context.session
    agent = tool_context._invocation_context.agent
    current_branch = tool_context._invocation_context.branch
    if not hasattr(agent, "canonical_model"):
        logger.warning("Agent has no model, skipping history compaction")
        return None

    events: List[Event] = session.events or []
    if len(events) < 2:
        return None

    opensage_session_id = get_opensage_session_id_from_context(tool_context)
    opensage_session = get_opensage_session(opensage_session_id)
    comp_cfg = getattr(opensage_session.config.history, "events_compaction", None)

    budget_chars = (
        getattr(comp_cfg, "max_history_summary_length", None) if comp_cfg else None
    )
    compaction_percent = getattr(comp_cfg, "compaction_percent", 50) if comp_cfg else 50

    # Trigger check: use consumption-side folded view of current branch full history
    try:
        from google.adk.flows.llm_flows.contents import (
            _get_contents as _adk_get_contents,
        )
    except Exception:
        _adk_get_contents = None

    folded_chars = None
    if _adk_get_contents is not None:
        try:
            agent_name = getattr(agent, "name", "") or ""
            folded_contents = _adk_get_contents(current_branch, events, agent_name)
            folded_chars = 0
            for content in folded_contents or []:
                if getattr(content, "parts", None):
                    for part in content.parts:
                        if getattr(part, "text", None):
                            folded_chars += len(part.text)
                        elif getattr(part, "function_call", None):
                            folded_chars += len(
                                json.dumps(
                                    {
                                        "name": part.function_call.name,
                                        "args": part.function_call.args,
                                    }
                                )
                            )
                        elif getattr(part, "function_response", None):
                            folded_chars += len(
                                json.dumps(
                                    {
                                        "name": part.function_response.name,
                                        "response": part.function_response.response,
                                    }
                                )
                            )
        except Exception as _e:
            logger.error(f"Failed to build folded contents for budget calc: {_e}")

    total_chars = (
        folded_chars
        if folded_chars is not None
        else sum(_estimate_event_chars(e) for e in events)
    )
    effective_budget = None
    if budget_chars is not None:
        # Mirror legacy behavior: subtract tool response threshold to reserve headroom
        try:
            tool_resp_budget = int(
                getattr(opensage_session.config.history, "max_tool_response_length", 0)
            )
        except Exception:
            tool_resp_budget = 0
        effective_budget = int(budget_chars) - tool_resp_budget
        if effective_budget < 0:
            effective_budget = 0
    trigger_by_budget = effective_budget is not None and total_chars > effective_budget
    if not trigger_by_budget:
        logger.info(
            f"No compaction triggered by budget (folded view): "
            f"{total_chars} <= {effective_budget}"
        )
        return None
    else:
        logger.info(
            f"Compaction triggered by budget (folded view): "
            f"{total_chars} > {effective_budget}"
        )

    # Determine last compaction boundary for windowing
    last_compaction_end_ts: float = float("-inf")
    for ev in reversed(events):
        if getattr(ev, "actions", None) and getattr(ev.actions, "compaction", None):
            if current_branch and ev.branch and ev.branch != current_branch:
                continue
            comp = ev.actions.compaction
            if getattr(comp, "end_timestamp", None) is not None:
                last_compaction_end_ts = max(last_compaction_end_ts, comp.end_timestamp)
            break

    # Find first user request timestamp on this branch (production-side constraint)
    first_user_ts: Optional[float] = None
    for ev in events:
        if current_branch and ev.branch and ev.branch != current_branch:
            continue
        if (
            getattr(ev, "author", None) == "user"
            and getattr(ev, "timestamp", None) is not None
        ):
            if first_user_ts is None or ev.timestamp < first_user_ts:
                first_user_ts = ev.timestamp

    # Candidates: after last end ts, same branch, exclude compaction events
    candidates: List[Event] = []
    for ev in events:
        if current_branch and ev.branch and ev.branch != current_branch:
            continue
        if getattr(ev, "actions", None) and getattr(ev.actions, "compaction", None):
            continue
        if ev.timestamp is None or ev.timestamp > last_compaction_end_ts:
            candidates.append(ev)

    if not candidates:
        logger.info(f"No compaction triggered by candidates: {candidates}")
        return None

    pct = int(compaction_percent)
    pct = max(0, min(100, pct))
    window_size = max(1, math.floor(len(candidates) * pct / 100)) if pct > 0 else 0
    if window_size <= 0:
        logger.info(
            f"No compaction triggered by window size: {window_size}, percentage: {pct}"
        )
        return None

    # Build a maximal legal prefix window [0..k) that guarantees pairing:
    # - Within the chosen prefix, every function_call id must have a matching
    #   function_response id, and vice versa.
    k = 0
    pending_calls: Set[str] = set()
    pending_resps: Set[str] = set()
    seen_calls: Set[str] = set()
    seen_resps: Set[str] = set()

    limit = min(window_size, len(candidates))
    for i in range(limit):
        ev = candidates[i]
        call_ids: List[str] = []
        resp_ids: List[str] = []
        if ev.content and ev.content.parts:
            for part in ev.content.parts:
                fc = getattr(part, "function_call", None)
                if fc and getattr(fc, "id", None):
                    call_ids.append(fc.id)
                fr = getattr(part, "function_response", None)
                if fr and getattr(fr, "id", None):
                    resp_ids.append(fr.id)

        # Update pending with calls first
        for cid in call_ids:
            seen_calls.add(cid)
            if cid in pending_resps:
                pending_resps.discard(cid)
            else:
                pending_calls.add(cid)

        # Then update with responses
        for rid in resp_ids:
            seen_resps.add(rid)
            if rid in pending_calls:
                pending_calls.discard(rid)
            else:
                pending_resps.add(rid)

        # If no pending on both sides, prefix [0..i] is legal
        if not pending_calls and not pending_resps:
            k = i + 1

    if k == 0:
        return None
    window_events: List[Event] = candidates[:k]

    # Enforce: window start must be > first_user_ts (if known)
    if first_user_ts is not None:
        trimmed_window: List[Event] = []
        for ev in window_events:
            ts = getattr(ev, "timestamp", None)
            if ts is None or ts > first_user_ts:
                trimmed_window.append(ev)
        if not trimmed_window:
            logger.info(
                f"No compaction triggered by events in window after user query: {len(window_events)}"
            )
            return None
        window_events = trimmed_window
    if len(window_events) <= 2:
        logger.info(
            f"No compaction triggered by actual events in window: {len(window_events)}"
        )
        return None

    # Choose summarization model
    model_name = getattr(opensage_session.config.llm, "summarize_model", None)
    if model_name:
        summarizer_model = resolve_model_spec(model_name, tool_context=tool_context)
    else:
        summarizer_model = agent.canonical_model

    summarizer = OpenSageFullEventSummarizer(model=summarizer_model)
    # Build folded full-history context text for the summarizer (current branch)
    folded_context_text: Optional[str] = None
    if _adk_get_contents is not None:
        try:
            agent_name = getattr(agent, "name", "") or ""
            folded_contents = _adk_get_contents(current_branch, events, agent_name)
            ctx_parts: List[str] = []
            for content in folded_contents or []:
                if getattr(content, "parts", None):
                    for part in content.parts:
                        if getattr(part, "text", None):
                            ctx_parts.append(part.text)
            folded_context_text = "\n".join(ctx_parts) if ctx_parts else None
        except Exception as _e:
            logger.warning(f"Failed to build folded context text: {_e}")

    # Build quota info for the summary
    quota_info = None
    inv_ctx = tool_context._invocation_context
    try:
        limit = int(
            getattr(getattr(inv_ctx, "run_config", None), "max_llm_calls", 0) or 0
        )
        used = int(
            getattr(
                getattr(inv_ctx, "_invocation_cost_manager", None),
                "_number_of_llm_calls",
                0,
            )
            or 0
        )
        if limit > 0:
            remaining = max(0, limit - used)
            quota_info = {
                "used": used,
                "limit": limit,
                "remaining": remaining,
            }
    except Exception as e:
        logger.warning(f"Failed to build quota info: {e}")

    compacted_content = await summarizer.maybe_summarize_events(
        events=window_events,
        folded_context_text=folded_context_text,
        quota_info=quota_info,
    )
    if not compacted_content:
        logger.info(f"No compaction generated by model, skipping compaction")
        return None

    start_ts = window_events[0].timestamp
    end_ts = window_events[-1].timestamp
    compaction = EventCompaction(
        start_timestamp=start_ts,
        end_timestamp=end_ts,
        compacted_content=compacted_content,
    )
    actions = EventActions(compaction=compaction)
    compaction_event = Event(
        author="user", actions=actions, invocation_id=Event.new_id()
    )

    # Attach current branch to compaction event
    compaction_event.branch = current_branch
    # Use current invocation_id for traceability if available
    compaction_event.invocation_id = getattr(
        tool_context._invocation_context,
        "invocation_id",
        compaction_event.invocation_id,
    )
    session_service = tool_context._invocation_context.session_service
    await session_service.append_event(session=session, event=compaction_event)

    # Neo4j persistence aligned with previous summarization semantics
    if is_neo4j_logging_enabled():
        from opensage.utils.neo4j_history_management import create_history_summary_node

        await create_history_summary_node(tool_context, compaction_event, window_events)

    logger.info(
        f"History compaction appended. Window size={len(window_events)}; "
        f"inv_id={compaction_event.invocation_id}"
    )
    return None


async def quota_after_tool_callback(tool, args, tool_context, tool_response):
    """
    Append quota countdown to tool responses (non-live):
    - If string result: append a line.
    - If dict result: inject _quota_info = {used, remaining, limit}.
    - Otherwise: no-op.
    """
    # Import here to avoid circular import
    from opensage.session import get_opensage_session

    try:
        opensage_session_id = get_opensage_session_id_from_context(tool_context)
        opensage_session = get_opensage_session(opensage_session_id)
    except Exception:
        opensage_session = None

    enable_quota = True
    if opensage_session and getattr(opensage_session, "config", None):
        try:
            enable_quota = bool(
                getattr(
                    getattr(opensage_session.config, "history", None),
                    "enable_quota_countdown",
                    True,
                )
            )
        except Exception:
            enable_quota = True
    if not enable_quota:
        return None

    inv_ctx = tool_context._invocation_context
    try:
        limit = int(
            getattr(getattr(inv_ctx, "run_config", None), "max_llm_calls", 0) or 0
        )
    except Exception:
        limit = 0
    try:
        used = int(
            getattr(
                getattr(inv_ctx, "_invocation_cost_manager", None),
                "_number_of_llm_calls",
                0,
            )
            or 0
        )
    except Exception:
        used = 0
    remaining = None
    if limit > 0:
        try:
            remaining = max(0, int(limit) - int(used))
        except Exception:
            remaining = None

    # Prefer dict mutation to avoid short-circuiting plugins.
    if isinstance(tool_response, dict):
        tool_response["_quota_info"] = {
            "used": int(used) if isinstance(used, (int, float)) else used,
            "remaining": (int(remaining) if remaining is not None else None),
            "limit": int(limit) if isinstance(limit, (int, float)) else limit,
        }
        return None

    # Fallback: do not attempt to override non-dict responses here.
    return None


def _extract_last_command_info(
    events: List[Event], output_dir: Optional[str] = None
) -> Optional[str]:
    """
    Extract the last function call and its response from events.
    This helps the next round understand what was attempted at the end.

    Args:
        events (List[Event]): List of session events
        output_dir (Optional[str]): Optional directory to write full output if truncated"""
    import json as _json
    from pathlib import Path

    MAX_OUTPUT_LENGTH = 400

    last_call = None
    last_response = None

    # Find the last function_call and its corresponding response
    for ev in reversed(events):
        if not ev.content or not ev.content.parts:
            continue
        for part in ev.content.parts:
            if getattr(part, "function_response", None) and last_response is None:
                fr = part.function_response
                last_response = {
                    "name": getattr(fr, "name", "unknown"),
                    "response": getattr(fr, "response", {}),
                }
            elif getattr(part, "function_call", None) and last_call is None:
                fc = part.function_call
                last_call = {
                    "name": getattr(fc, "name", "unknown"),
                    "args": getattr(fc, "args", {}),
                }
        # Stop once we have both
        if last_call and last_response:
            break

    if not last_call:
        return None

    lines = []

    # Format the command
    try:
        args_str = _json.dumps(last_call["args"], ensure_ascii=False, indent=2)
    except Exception:
        args_str = str(last_call["args"])
    lines.append(f"**Command:** `{last_call['name']}`")
    lines.append(f"**Arguments:**\n```\n{args_str}\n```")

    # Format the response (removing _quota_info)
    if last_response:
        resp = last_response["response"]
        if isinstance(resp, dict):
            # Remove quota info to reduce noise
            resp = {k: v for k, v in resp.items() if k != "_quota_info"}

            # Handle long output - write to file if needed
            if (
                "output" in resp
                and isinstance(resp["output"], str)
                and len(resp["output"]) > MAX_OUTPUT_LENGTH
            ):
                full_output = resp["output"]
                output_file_path = None

                # Write full output to file if output_dir is provided
                if output_dir:
                    try:
                        output_file = Path(output_dir) / "last_command_full_output.txt"
                        output_file.write_text(full_output)
                        output_file_path = str(output_file)
                    except Exception as e:
                        logger.warning(
                            f"Failed to write last command output to file: {e}"
                        )

                # Truncate the output in response
                resp["output"] = full_output[:MAX_OUTPUT_LENGTH] + "... [truncated]"
                if output_file_path:
                    resp["_full_output_file"] = output_file_path

        try:
            resp_str = _json.dumps(resp, ensure_ascii=False, indent=2)
        except Exception:
            resp_str = str(resp)
        lines.append(f"**Result:**\n```\n{resp_str}\n```")

        # Add file reference note if output was truncated to file
        if isinstance(resp, dict) and "_full_output_file" in resp:
            lines.append(f"\nFull output saved to: `{resp['_full_output_file']}`")
            lines.append("You can read this file to see the complete output.")

        # Add a note if the command failed
        if isinstance(last_response["response"], dict):
            success = last_response["response"].get("success", True)
            exit_code = last_response["response"].get("exit_code")
            if not success or (exit_code is not None and exit_code != 0):
                lines.append("\n**Note:** This command failed.")

    return "\n".join(lines)


async def generate_final_compaction(
    events: List[Event],
    model: LiteLlm,
    branch: Optional[str] = None,
    output_dir: Optional[str] = None,
    quota_info: Optional[dict] = None,
) -> Optional[str]:
    """
    Generate a final compaction summary of all session events.

    This is designed to be called when the agent reaches max_llm_calls,
    to create a summary of everything the agent explored/learned.

    Args:
        events (List[Event]): List of session events to summarize
        model (LiteLlm): LLM model to use for summarization
        branch (Optional[str]): Optional branch to filter events
        output_dir (Optional[str]): Optional directory to write full output files if truncated
        quota_info (Optional[dict]): Optional dict with keys: used, limit, remaining
    Returns:
        Optional[str]: Summary text or None if summarization fails
    """
    if not events:
        logger.warning("No events to summarize for final compaction")
        return None

    # Filter events by branch if specified
    if branch:
        events = [e for e in events if not e.branch or e.branch == branch]

    # Filter out compaction events (we want original content)
    filtered_events = []
    for ev in events:
        if getattr(ev, "actions", None) and getattr(ev.actions, "compaction", None):
            continue
        filtered_events.append(ev)

    if not filtered_events:
        logger.warning("No non-compaction events to summarize")
        return None

    summarizer = OpenSageFullEventSummarizer(model=model)

    # Generate summary
    compacted_content = await summarizer.maybe_summarize_events(
        events=filtered_events,
        folded_context_text=None,
        quota_info=quota_info,
    )

    if not compacted_content or not compacted_content.parts:
        logger.warning("Final compaction produced no content")
        return None

    # Extract text from the content
    summary_text = ""
    for part in compacted_content.parts:
        if getattr(part, "text", None):
            summary_text += part.text

    # Append the last command and its output to help the next round
    last_command_info = _extract_last_command_info(
        filtered_events, output_dir=output_dir
    )
    if last_command_info:
        summary_text += "\n\n### Last Command Before Session Ended\n"
        summary_text += last_command_info

    logger.info(
        f"Final compaction generated: {len(summary_text)} chars from {len(filtered_events)} events"
    )
    return summary_text
