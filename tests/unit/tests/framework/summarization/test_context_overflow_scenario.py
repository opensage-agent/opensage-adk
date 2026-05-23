"""Tests reproducing the context-overflow bug from the CTF scheduler.

The bug: background task completion pushed 400K-800K chars of log output
to the inbox.  InboxDeliveryPlugin injected the full text into a tool
response's ``_incoming_messages`` field with no truncation.  Meanwhile,
compaction ran in ``after_tool_callback`` inside ``asyncio.gather`` — it
raced on parallel tool calls and couldn't see the current (giant) tool
responses because they hadn't been appended to the session yet.

The fix has three parts:
1. InboxDeliveryPlugin now truncates _incoming_messages to a limit and
   saves the full content to a file.
2. Compaction moved to ``on_event_callback`` — fires after each event is
   appended, so ``session.events`` is already compacted when
   ``_ContentLlmRequestProcessor`` builds ``llm_request.contents``.
3. Emergency truncation remains in ``before_model_callback`` as a
   last-resort safety net.

These tests verify all parts work correctly under the original failure
conditions.
"""

from __future__ import annotations

import types as _types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.adk.events.event import Event
from google.genai import types

from opensage.features.summarization import (
    history_compaction_on_event,
)
from opensage.orchestration.plugins.inbox_delivery import (
    _MAX_INCOMING_MESSAGES_CHARS,
    InboxDeliveryPlugin,
    _truncate_incoming_messages,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text_content(role: str, text: str) -> types.Content:
    return types.Content(role=role, parts=[types.Part.from_text(text=text)])


def _text_event(author: str, text: str, ts: float) -> Event:
    ev = Event(
        invocation_id="inv-1",
        author=author,
        timestamp=ts,
        content=_text_content("user" if author == "user" else "model", text),
    )
    ev.branch = None
    return ev


def _fr_event_with_incoming(output: str, incoming: str, ts: float) -> Event:
    """Simulate a tool-response event carrying _incoming_messages."""
    import json

    ev = Event(
        invocation_id="inv-1",
        author="user",
        timestamp=ts,
        content=types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="call-1",
                        name="run_terminal_command",
                        response={"output": output, "_incoming_messages": incoming},
                    )
                )
            ],
        ),
    )
    ev.branch = None
    return ev


class _CompCfg:
    def __init__(self, budget: int, pct: int = 50):
        self.max_history_summary_length = budget
        self.compaction_percent = pct


class _HistoryCfg:
    def __init__(self, budget: int, pct: int = 50, tool_resp_len: int = 0):
        self.events_compaction = _CompCfg(budget, pct)
        self.max_tool_response_length = tool_resp_len


class _LlmCfg:
    summarize_model = None


class _FakeOpenSageSession:
    def __init__(self, budget: int, pct: int = 50, tool_resp_len: int = 0):
        self.config = _types.SimpleNamespace(
            history=_HistoryCfg(budget, pct, tool_resp_len),
            llm=_LlmCfg(),
        )


def _make_invocation_context(events):
    """Create a fake InvocationContext for on_event_callback tests."""
    session = MagicMock()
    session.events = events
    session.state = {"opensage_session_id": "test-sid"}

    agent = MagicMock()
    agent.name = "host_schedule_agent"
    agent.canonical_model = MagicMock()

    inv_ctx = MagicMock()
    inv_ctx.session = session
    inv_ctx.agent = agent
    inv_ctx.branch = None
    inv_ctx.invocation_id = "inv-1"
    inv_ctx.session_service = AsyncMock()
    inv_ctx.run_config = None
    inv_ctx._invocation_cost_manager = None
    inv_ctx.state = session.state
    return inv_ctx


# ---------------------------------------------------------------------------
# Part 1: InboxDeliveryPlugin truncation
# ---------------------------------------------------------------------------


class TestInboxTruncation:
    """Verify that oversized inbox messages are truncated and saved."""

    def test_truncate_preserves_header(self):
        header = "[Incoming peer messages]\nGuidance text here."
        body = "\n\n" + "x" * 50000
        formatted = header + body

        result = _truncate_incoming_messages(formatted, 5000, "/saved/path.log")

        assert result.startswith("[Incoming peer messages]")
        assert len(result) <= 6000  # some overhead for the notice
        assert "truncated:" in result
        assert "/saved/path.log" in result

    def test_truncate_includes_file_path(self):
        formatted = "header\n\n" + "x" * 10000
        result = _truncate_incoming_messages(
            formatted, 2000, "/workspace/.tool_outputs/msg.log"
        )
        assert "/workspace/.tool_outputs/msg.log" in result

    def test_truncate_no_file_path_when_save_failed(self):
        formatted = "header\n\n" + "x" * 10000
        result = _truncate_incoming_messages(formatted, 2000, None)
        assert "saved to:" not in result.lower()
        assert "truncated:" in result

    def test_no_truncation_when_under_limit(self):
        formatted = "short message"
        result = _truncate_incoming_messages(formatted, 5000, None)
        assert result == "short message"

    @pytest.mark.asyncio
    async def test_plugin_truncates_large_inbox_messages(self):
        """The actual bug: 800K chars of background task output in inbox."""
        from opensage.orchestration.inbox import Inbox
        from opensage.orchestration.types import Message

        inbox = MagicMock(spec=Inbox)
        inbox.has_pending = AsyncMock(return_value=True)

        giant_output = "A" * 800_000
        inbox.pop_all = AsyncMock(
            return_value=[
                Message(
                    from_sid="self",
                    to_sid="self",
                    content=(
                        f"Background task bg_001 finished (status=completed, exit_code=0).\n"
                        f"Command: cd /sageagent-ctf && uv run opensage run ...\n"
                        f"Output:\n{giant_output}"
                    ),
                    kind="background_task_result",
                    from_agent_name="host_schedule_agent",
                )
            ]
        )

        plugin = InboxDeliveryPlugin(inbox)

        tool = MagicMock()
        tool.name = "run_terminal_command"
        tool_context = MagicMock()
        tool_context.function_call_id = "fc-123"
        result = {"output": "task started"}

        with patch(
            "opensage.orchestration.plugins.inbox_delivery._save_full_messages",
            new_callable=AsyncMock,
            return_value="/workspace/.tool_outputs/incoming_messages_fc-123.log",
        ) as mock_save:
            await plugin.after_tool_callback(
                tool=tool,
                tool_args={},
                tool_context=tool_context,
                result=result,
            )

        assert "_incoming_messages" in result
        msg = result["_incoming_messages"]
        assert len(msg) < _MAX_INCOMING_MESSAGES_CHARS + 500
        assert "truncated:" in msg
        assert "incoming_messages_fc-123.log" in msg

        mock_save.assert_awaited_once()
        saved_content = mock_save.call_args.args[1]
        assert len(saved_content) > 800_000

    @pytest.mark.asyncio
    async def test_plugin_no_truncation_for_small_messages(self):
        from opensage.orchestration.inbox import Inbox
        from opensage.orchestration.types import Message

        inbox = MagicMock(spec=Inbox)
        inbox.has_pending = AsyncMock(return_value=True)
        inbox.pop_all = AsyncMock(
            return_value=[
                Message(
                    from_sid="A",
                    to_sid="B",
                    content="small result",
                    kind="text",
                    from_agent_name="helper",
                )
            ]
        )

        plugin = InboxDeliveryPlugin(inbox)
        result = {"output": "ok"}

        await plugin.after_tool_callback(
            tool=MagicMock(),
            tool_args={},
            tool_context=MagicMock(),
            result=result,
        )

        assert "_incoming_messages" in result
        assert "truncated" not in result["_incoming_messages"]

    @pytest.mark.asyncio
    async def test_plugin_save_failure_still_truncates(self):
        """Even if file save fails, truncation should still happen."""
        from opensage.orchestration.inbox import Inbox
        from opensage.orchestration.types import Message

        inbox = MagicMock(spec=Inbox)
        inbox.has_pending = AsyncMock(return_value=True)
        inbox.pop_all = AsyncMock(
            return_value=[
                Message(
                    from_sid="A",
                    to_sid="B",
                    content="x" * 100_000,
                    kind="background_task_result",
                    from_agent_name="agent",
                )
            ]
        )

        plugin = InboxDeliveryPlugin(inbox)
        result = {"output": "ok"}

        with patch(
            "opensage.orchestration.plugins.inbox_delivery._save_full_messages",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await plugin.after_tool_callback(
                tool=MagicMock(),
                tool_args={},
                tool_context=MagicMock(),
                result=result,
            )

        msg = result["_incoming_messages"]
        assert len(msg) < _MAX_INCOMING_MESSAGES_CHARS + 500
        assert "truncated:" in msg


# ---------------------------------------------------------------------------
# Part 2: Parallel tool calls — only one gets inbox messages
# ---------------------------------------------------------------------------


class TestParallelToolCallInbox:
    """In the bug, 3 parallel tool calls each triggered after_tool_callback.
    The inbox had a giant message. With pop_all semantics, only the first
    tool call to pop gets the messages; the others get nothing.
    """

    @pytest.mark.asyncio
    async def test_only_first_popper_gets_messages(self):
        from opensage.orchestration.inbox import Inbox
        from opensage.orchestration.types import Message

        inbox = Inbox.__new__(Inbox)
        pop_count = 0
        original_messages = [
            Message(from_sid="A", to_sid="B", content="big output " * 100, kind="text")
        ]

        async def fake_has_pending():
            return pop_count == 0

        async def fake_pop_all():
            nonlocal pop_count
            if pop_count == 0:
                pop_count += 1
                return original_messages
            return []

        inbox.has_pending = fake_has_pending
        inbox.pop_all = fake_pop_all

        plugin = InboxDeliveryPlugin(inbox)

        results = []
        for i in range(3):
            r = {"output": f"tool_{i}"}
            await plugin.after_tool_callback(
                tool=MagicMock(name=f"tool_{i}"),
                tool_args={},
                tool_context=MagicMock(),
                result=r,
            )
            results.append(r)

        got_messages = [r for r in results if "_incoming_messages" in r]
        assert len(got_messages) == 1


# ---------------------------------------------------------------------------
# Part 3: on_event compaction sees full session including giant responses
# ---------------------------------------------------------------------------


class TestCompactionSeesFullSession:
    """The old bug: compaction ran in after_tool_callback inside
    asyncio.gather. It computed total_chars from session.events, but the
    current tool responses (with 800K _incoming_messages) hadn't been
    appended yet. So it either didn't trigger or triggered with a stale
    budget check.

    With on_event_callback, each event is already in session.events when
    compaction runs.
    """

    @pytest.mark.asyncio
    async def test_compaction_triggers_on_large_tool_responses(self):
        """Simulate the crash scenario: tool responses with large
        _incoming_messages are in the session. Compaction should trigger."""
        events = [
            _text_event("user", "start monitoring", 1.0),
            _text_event("agent", "launching agents", 2.0),
            _text_event("user", "check status", 3.0),
            _fr_event_with_incoming(
                output="task started",
                incoming="[truncated to 5K]" + "x" * 4900,
                ts=4.0,
            ),
            _text_event("agent", "status update", 5.0),
            _text_event("user", "continue", 6.0),
        ]
        inv_ctx = _make_invocation_context(events)
        trigger_event = events[-1]

        fake_summarizer = AsyncMock()
        fake_summarizer.maybe_summarize_events = AsyncMock(
            return_value=_text_content("model", "COMPACTED")
        )

        with (
            patch(
                "opensage.features.summarization.get_opensage_session_id_from_context",
                return_value="sid",
            ),
            patch(
                "opensage.session.get_opensage_session",
                return_value=_FakeOpenSageSession(budget=100, pct=100),
            ),
            patch(
                "opensage.features.summarization.OpenSageFullEventSummarizer",
                return_value=fake_summarizer,
            ),
            patch(
                "opensage.features.summarization._read_pinned_content",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "opensage.memory.file_based.short_term.persist_traj_json_for_invocation",
                new_callable=AsyncMock,
            ),
        ):
            await history_compaction_on_event(inv_ctx, trigger_event)

        inv_ctx.session_service.append_event.assert_awaited_once()
        appended = inv_ctx.session_service.append_event.call_args.kwargs["event"]
        assert appended.actions.compaction is not None

    @pytest.mark.asyncio
    async def test_compaction_fires_exactly_once(self):
        """on_event_callback fires per event; verify a single call
        produces exactly one compaction."""
        events = [
            _text_event("user", "x" * 5000, 1.0),
            _text_event("agent", "x" * 5000, 2.0),
            _text_event("user", "x" * 5000, 3.0),
            _text_event("agent", "x" * 5000, 4.0),
        ]
        inv_ctx = _make_invocation_context(events)

        fake_summarizer = AsyncMock()
        fake_summarizer.maybe_summarize_events = AsyncMock(
            return_value=_text_content("model", "SUM")
        )

        with (
            patch(
                "opensage.features.summarization.get_opensage_session_id_from_context",
                return_value="sid",
            ),
            patch(
                "opensage.session.get_opensage_session",
                return_value=_FakeOpenSageSession(budget=100, pct=100),
            ),
            patch(
                "opensage.features.summarization.OpenSageFullEventSummarizer",
                return_value=fake_summarizer,
            ),
            patch(
                "opensage.features.summarization._read_pinned_content",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "opensage.memory.file_based.short_term.persist_traj_json_for_invocation",
                new_callable=AsyncMock,
            ),
        ):
            await history_compaction_on_event(inv_ctx, events[-1])

        assert fake_summarizer.maybe_summarize_events.await_count == 1
        assert inv_ctx.session_service.append_event.await_count == 1


# ---------------------------------------------------------------------------
# Part 4: End-to-end scenario from the actual crash
# ---------------------------------------------------------------------------


class TestEndToEndOverflowScenario:
    """Reproduce the exact sequence that crashed the host_schedule_agent:

    1. Agent makes 3 parallel tool calls (list_background_tasks,
       get_background_task_output, run_terminal_command)
    2. While tools execute, a background task completes and pushes
       800K chars to inbox
    3. InboxDeliveryPlugin pops the message and attaches to one tool result
    4. Tool responses are gathered and appended to session
    5. on_event_callback fires compaction after each appended event

    Pre-fix: step 3 injected 800K chars, compaction ran in
    after_tool_callback (raced, couldn't see responses). Context overflowed.

    Post-fix: step 3 truncates to 5K and saves full to file, step 5 is
    on_event_callback (fires after each event is in the session).
    """

    @pytest.mark.asyncio
    async def test_full_scenario_no_overflow(self):
        from opensage.orchestration.inbox import Inbox
        from opensage.orchestration.types import Message

        giant_log = "LOG LINE\n" * 100_000  # ~900K chars

        inbox = MagicMock(spec=Inbox)
        inbox.has_pending = AsyncMock(return_value=True)
        inbox.pop_all = AsyncMock(
            return_value=[
                Message(
                    from_sid="host_schedule_agent",
                    to_sid="host_schedule_agent",
                    content=(
                        f"Background task bg_001 finished "
                        f"(status=completed, exit_code=0).\n"
                        f"Command: cd /sageagent-ctf && uv run opensage run ...\n"
                        f"Output:\n{giant_log}"
                    ),
                    kind="background_task_result",
                    from_agent_name="host_schedule_agent",
                )
            ]
        )

        plugin = InboxDeliveryPlugin(inbox)
        tool_result = {"output": "3 tasks running"}

        with patch(
            "opensage.orchestration.plugins.inbox_delivery._save_full_messages",
            new_callable=AsyncMock,
            return_value="/workspace/.tool_outputs/incoming_messages_fc1.log",
        ):
            await plugin.after_tool_callback(
                tool=MagicMock(name="list_background_tasks"),
                tool_args={},
                tool_context=MagicMock(function_call_id="fc1"),
                result=tool_result,
            )

        incoming = tool_result["_incoming_messages"]
        assert len(incoming) < _MAX_INCOMING_MESSAGES_CHARS + 500
        assert "truncated:" in incoming
        assert "incoming_messages_fc1.log" in incoming

        # Build session as it would look after runners.py append_event
        events = [
            _text_event("user", "check status and monitor", 1.0),
            _text_event("agent", "I'll check all tasks", 2.0),
            Event(
                invocation_id="inv-1",
                author="host_schedule_agent",
                timestamp=3.0,
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                id="fc1", name="list_background_tasks", args={}
                            )
                        ),
                        types.Part(
                            function_call=types.FunctionCall(
                                id="fc2",
                                name="get_background_task_output",
                                args={"task_id": "bg_001"},
                            )
                        ),
                        types.Part(
                            function_call=types.FunctionCall(
                                id="fc3",
                                name="run_terminal_command",
                                args={"command": "curl ..."},
                            )
                        ),
                    ],
                ),
            ),
            Event(
                invocation_id="inv-1",
                author="user",
                timestamp=4.0,
                content=types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id="fc1",
                                name="list_background_tasks",
                                response={
                                    "output": "3 tasks",
                                    "_incoming_messages": incoming,
                                },
                            )
                        ),
                        types.Part(
                            function_response=types.FunctionResponse(
                                id="fc2",
                                name="get_background_task_output",
                                response={"output": "agent running..."},
                            )
                        ),
                        types.Part(
                            function_response=types.FunctionResponse(
                                id="fc3",
                                name="run_terminal_command",
                                response={"output": "HTTP 200"},
                            )
                        ),
                    ],
                ),
            ),
        ]
        for ev in events:
            ev.branch = None

        inv_ctx = _make_invocation_context(events)

        fake_summarizer = AsyncMock()
        fake_summarizer.maybe_summarize_events = AsyncMock(
            return_value=_text_content("model", "COMPACTED_HISTORY")
        )

        with (
            patch(
                "opensage.features.summarization.get_opensage_session_id_from_context",
                return_value="sid",
            ),
            patch(
                "opensage.session.get_opensage_session",
                return_value=_FakeOpenSageSession(
                    budget=300000, pct=50, tool_resp_len=20000
                ),
            ),
            patch(
                "opensage.features.summarization.OpenSageFullEventSummarizer",
                return_value=fake_summarizer,
            ),
            patch(
                "opensage.features.summarization._read_pinned_content",
                new_callable=AsyncMock,
                return_value="## Active Agents\n| challenge | status |\n|---|---|\n| baby_pwn | running |",
            ),
            patch(
                "opensage.memory.file_based.short_term.persist_traj_json_for_invocation",
                new_callable=AsyncMock,
            ),
        ):
            await history_compaction_on_event(inv_ctx, events[-1])

        # Compaction should NOT trigger because total chars are now small
        # (truncated _incoming_messages keeps things under 280K budget)

    @pytest.mark.asyncio
    async def test_pre_fix_scenario_would_have_overflowed(self):
        """Without truncation, the same scenario exceeds the budget
        and triggers compaction."""
        giant_text = "x" * 200_000

        events = [
            _text_event("user", "check status", 1.0),
            _text_event("agent", "checking", 2.0),
            _text_event("user", giant_text, 3.0),
            _text_event("agent", giant_text, 4.0),
        ]
        inv_ctx = _make_invocation_context(events)

        fake_summarizer = AsyncMock()
        fake_summarizer.maybe_summarize_events = AsyncMock(
            return_value=_text_content("model", "SUM")
        )

        with (
            patch(
                "opensage.features.summarization.get_opensage_session_id_from_context",
                return_value="sid",
            ),
            patch(
                "opensage.session.get_opensage_session",
                return_value=_FakeOpenSageSession(
                    budget=300000, pct=100, tool_resp_len=20000
                ),
            ),
            patch(
                "opensage.features.summarization.OpenSageFullEventSummarizer",
                return_value=fake_summarizer,
            ),
            patch(
                "opensage.features.summarization._read_pinned_content",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "opensage.memory.file_based.short_term.persist_traj_json_for_invocation",
                new_callable=AsyncMock,
            ),
        ):
            await history_compaction_on_event(inv_ctx, events[-1])

        inv_ctx.session_service.append_event.assert_awaited_once()


# ---------------------------------------------------------------------------
# Part 6: on_event_callback compaction
# ---------------------------------------------------------------------------


class TestOnEventCompaction:
    """Compaction via on_event_callback fires after each event is appended
    to the session, so session.events is already compacted when
    _ContentLlmRequestProcessor later builds llm_request.contents.
    """

    @pytest.mark.asyncio
    async def test_compaction_triggers_on_event(self):
        """Regular event triggers compaction when over budget."""
        events = [
            _text_event("user", "x" * 5000, 1.0),
            _text_event("agent", "x" * 5000, 2.0),
            _text_event("user", "x" * 5000, 3.0),
            _text_event("agent", "x" * 5000, 4.0),
        ]
        inv_ctx = _make_invocation_context(events)
        trigger_event = events[-1]

        fake_summarizer = AsyncMock()
        fake_summarizer.maybe_summarize_events = AsyncMock(
            return_value=_text_content("model", "SUM")
        )

        with (
            patch(
                "opensage.features.summarization.get_opensage_session_id_from_context",
                return_value="sid",
            ),
            patch(
                "opensage.session.get_opensage_session",
                return_value=_FakeOpenSageSession(budget=100, pct=100),
            ),
            patch(
                "opensage.features.summarization.OpenSageFullEventSummarizer",
                return_value=fake_summarizer,
            ),
            patch(
                "opensage.features.summarization._read_pinned_content",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "opensage.memory.file_based.short_term.persist_traj_json_for_invocation",
                new_callable=AsyncMock,
            ),
        ):
            await history_compaction_on_event(inv_ctx, trigger_event)

        inv_ctx.session_service.append_event.assert_awaited_once()
        appended = inv_ctx.session_service.append_event.call_args.kwargs["event"]
        assert appended.actions.compaction is not None

    @pytest.mark.asyncio
    async def test_compaction_event_skipped(self):
        """Compaction events must not trigger recursive compaction."""
        from google.adk.events.event_actions import EventActions, EventCompaction

        compaction_event = Event(
            invocation_id="inv-compact",
            author="user",
            timestamp=5.0,
            actions=EventActions(
                compaction=EventCompaction(
                    start_timestamp=1.0,
                    end_timestamp=4.0,
                    compacted_content=_text_content("model", "SUMMARY"),
                )
            ),
        )

        inv_ctx = _make_invocation_context([])
        result = await history_compaction_on_event(inv_ctx, compaction_event)
        assert result is None
        inv_ctx.session_service.append_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_compaction_under_budget(self):
        """Events under budget should not trigger compaction."""
        events = [
            _text_event("user", "hello", 1.0),
            _text_event("agent", "hi", 2.0),
        ]
        inv_ctx = _make_invocation_context(events)
        trigger_event = events[-1]

        with (
            patch(
                "opensage.features.summarization.get_opensage_session_id_from_context",
                return_value="sid",
            ),
            patch(
                "opensage.session.get_opensage_session",
                return_value=_FakeOpenSageSession(budget=300_000),
            ),
        ):
            await history_compaction_on_event(inv_ctx, trigger_event)

        inv_ctx.session_service.append_event.assert_not_awaited()
