"""Tests for history_compaction_on_event.

Exercises the budget check, candidate windowing, function-call pairing,
pinned-content embedding, and the compaction event that gets appended.
"""

from __future__ import annotations

import types as _types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions, EventCompaction
from google.genai import types

from opensage.features.summarization import history_compaction_on_event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text_content(role: str, text: str) -> types.Content:
    return types.Content(role=role, parts=[types.Part.from_text(text=text)])


def _text_event(
    author: str, text: str, ts: float, inv_id: str = "inv-1", branch: str | None = None
) -> Event:
    ev = Event(
        invocation_id=inv_id,
        author=author,
        timestamp=ts,
        content=_text_content("user" if author == "user" else "model", text),
    )
    ev.branch = branch
    return ev


def _fc_event(
    call_id: str, name: str, ts: float, inv_id: str = "inv-1", branch: str | None = None
) -> Event:
    """Event with a function_call part."""
    ev = Event(
        invocation_id=inv_id,
        author="agent",
        timestamp=ts,
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(id=call_id, name=name, args={})
                )
            ],
        ),
    )
    ev.branch = branch
    return ev


def _fr_event(
    call_id: str, name: str, ts: float, inv_id: str = "inv-1", branch: str | None = None
) -> Event:
    """Event with a function_response part."""
    ev = Event(
        invocation_id=inv_id,
        author="user",
        timestamp=ts,
        content=types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=call_id, name=name, response={"result": "ok"}
                    )
                )
            ],
        ),
    )
    ev.branch = branch
    return ev


def _compaction_event(
    start_ts: float, end_ts: float, branch: str | None = None
) -> Event:
    compaction = EventCompaction(
        start_timestamp=start_ts,
        end_timestamp=end_ts,
        compacted_content=_text_content("model", "SUMMARY"),
    )
    ev = Event(
        invocation_id="inv-compact",
        author="user",
        timestamp=end_ts + 0.01,
        actions=EventActions(compaction=compaction),
    )
    ev.branch = branch
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


def _make_invocation_context(events, branch=None, has_model=True):
    session = MagicMock()
    session.events = events
    session.state = {"opensage_session_id": "test-sid"}

    agent = MagicMock()
    agent.name = "test_agent"
    if has_model:
        agent.canonical_model = MagicMock()
    else:
        del agent.canonical_model

    inv_ctx = MagicMock()
    inv_ctx.session = session
    inv_ctx.agent = agent
    inv_ctx.branch = branch
    inv_ctx.invocation_id = "inv-1"
    inv_ctx.session_service = AsyncMock()
    inv_ctx.run_config = None
    inv_ctx._invocation_cost_manager = None
    inv_ctx.state = session.state
    return inv_ctx


# ---------------------------------------------------------------------------
# Tests: early returns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_none_when_no_canonical_model():
    ev = _text_event("user", "hello", 1.0)
    inv_ctx = _make_invocation_context([ev], has_model=False)
    result = await history_compaction_on_event(inv_ctx, ev)
    assert result is None


@pytest.mark.asyncio
async def test_returns_none_when_fewer_than_two_events():
    ev = _text_event("user", "hello", 1.0)
    inv_ctx = _make_invocation_context([ev])
    with patch(
        "opensage.features.summarization.get_opensage_session_id_from_context",
        return_value="sid",
    ):
        result = await history_compaction_on_event(inv_ctx, ev)
    assert result is None


@pytest.mark.asyncio
async def test_returns_none_when_no_compaction_config():
    events = [
        _text_event("user", "hello", 1.0),
        _text_event("agent", "world", 2.0),
    ]
    inv_ctx = _make_invocation_context(events)

    fake_session = MagicMock()
    fake_session.config.history.events_compaction = None

    with (
        patch(
            "opensage.features.summarization.get_opensage_session_id_from_context",
            return_value="sid",
        ),
        patch(
            "opensage.session.get_opensage_session",
            return_value=fake_session,
        ),
    ):
        result = await history_compaction_on_event(inv_ctx, events[-1])
    assert result is None


# ---------------------------------------------------------------------------
# Tests: budget trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_compaction_when_under_budget():
    events = [
        _text_event("user", "short", 1.0),
        _text_event("agent", "reply", 2.0),
        _text_event("user", "more", 3.0),
    ]
    inv_ctx = _make_invocation_context(events)

    with (
        patch(
            "opensage.features.summarization.get_opensage_session_id_from_context",
            return_value="sid",
        ),
        patch(
            "opensage.session.get_opensage_session",
            return_value=_FakeOpenSageSession(budget=999999),
        ),
    ):
        result = await history_compaction_on_event(inv_ctx, events[-1])
    assert result is None


@pytest.mark.asyncio
async def test_compaction_triggers_when_over_budget():
    long_text = "x" * 5000
    events = [
        _text_event("user", long_text, 1.0),
        _text_event("agent", long_text, 2.0),
        _text_event("user", long_text, 3.0),
        _text_event("agent", long_text, 4.0),
        _text_event("user", long_text, 5.0),
        _text_event("agent", long_text, 6.0),
    ]
    inv_ctx = _make_invocation_context(events)

    fake_summarizer = AsyncMock()
    fake_summarizer.maybe_summarize_events = AsyncMock(
        return_value=_text_content("model", "COMPACTED_SUMMARY")
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
        result = await history_compaction_on_event(inv_ctx, events[-1])

    assert result is None
    inv_ctx.session_service.append_event.assert_awaited_once()
    appended_event = inv_ctx.session_service.append_event.call_args.kwargs["event"]
    assert appended_event.actions is not None
    assert appended_event.actions.compaction is not None
    assert (
        "COMPACTED_SUMMARY"
        in appended_event.actions.compaction.compacted_content.parts[0].text
    )


# ---------------------------------------------------------------------------
# Tests: effective budget = max_history - max_tool_response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_effective_budget_subtracts_tool_response_length():
    text = "x" * 300
    events = [
        _text_event("user", text, 1.0),
        _text_event("agent", text, 2.0),
        _text_event("user", text, 3.0),
        _text_event("agent", text, 4.0),
    ]
    inv_ctx = _make_invocation_context(events)

    # budget=1000, tool_resp_len=200 => effective=800
    # total chars = 4*300 = 1200 > 800, should trigger
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
            return_value=_FakeOpenSageSession(budget=1000, pct=100, tool_resp_len=200),
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
# Tests: candidate windowing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_excludes_compaction_events_from_candidates():
    long_text = "x" * 5000
    events = [
        _compaction_event(0.0, 1.0),
        _text_event("user", long_text, 2.0),
        _text_event("agent", long_text, 3.0),
        _text_event("user", long_text, 4.0),
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
        # Trigger with a non-compaction event
        await history_compaction_on_event(inv_ctx, events[-1])

    # Verify: summarizer received 3 candidate events (not the compaction event)
    call_args = fake_summarizer.maybe_summarize_events.call_args
    window_events = call_args.kwargs["events"]
    assert len(window_events) == 3
    for ev in window_events:
        assert ev.actions is None or getattr(ev.actions, "compaction", None) is None


@pytest.mark.asyncio
async def test_candidates_only_after_last_compaction_boundary():
    long_text = "x" * 5000
    events = [
        _text_event("user", long_text, 1.0),
        _text_event("agent", long_text, 2.0),
        _compaction_event(1.0, 2.0),
        _text_event("user", long_text, 3.0),
        _text_event("agent", long_text, 4.0),
        _text_event("user", long_text, 5.0),
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

    call_args = fake_summarizer.maybe_summarize_events.call_args
    window_events = call_args.kwargs["events"]
    # Only events after compaction boundary (ts=2.0): ts=3.0, 4.0, 5.0
    assert len(window_events) == 3
    assert all(ev.timestamp > 2.0 for ev in window_events)


# ---------------------------------------------------------------------------
# Tests: function call/response pairing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_window_respects_function_call_pairing():
    """Window stops before unpaired function calls."""
    long_text = "x" * 5000
    events = [
        _text_event("user", long_text, 1.0),
        _text_event("agent", long_text, 2.0),
        _fc_event("call-1", "tool_a", 3.0),
        _fr_event("call-1", "tool_a", 4.0),
        _fc_event("call-2", "tool_b", 5.0),
        # No response for call-2 — window must not include it
        _text_event("user", long_text, 6.0),
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

    call_args = fake_summarizer.maybe_summarize_events.call_args
    window_events = call_args.kwargs["events"]
    # Window should be [ev0, ev1, fc1, fr1] — 4 events (paired)
    # It cannot include fc2 (unpaired) even though pct=100
    assert len(window_events) == 4
    timestamps = [ev.timestamp for ev in window_events]
    assert timestamps == [1.0, 2.0, 3.0, 4.0]


@pytest.mark.asyncio
async def test_window_too_small_returns_none():
    """When paired window has <= 2 events, no compaction."""
    events = [
        _text_event("user", "x" * 5000, 1.0),
        _fc_event("call-1", "tool_a", 2.0),
        # No response — window can only be [ev0] which is <= 2
    ]
    inv_ctx = _make_invocation_context(events)

    with (
        patch(
            "opensage.features.summarization.get_opensage_session_id_from_context",
            return_value="sid",
        ),
        patch(
            "opensage.session.get_opensage_session",
            return_value=_FakeOpenSageSession(budget=100, pct=100),
        ),
    ):
        result = await history_compaction_on_event(inv_ctx, events[-1])
    assert result is None


# ---------------------------------------------------------------------------
# Tests: compaction event structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_event_has_correct_timestamps():
    long_text = "x" * 5000
    events = [
        _text_event("user", long_text, 10.0),
        _text_event("agent", long_text, 20.0),
        _text_event("user", long_text, 30.0),
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

    appended_event = inv_ctx.session_service.append_event.call_args.kwargs["event"]
    comp = appended_event.actions.compaction
    assert comp.start_timestamp == 10.0
    assert comp.end_timestamp == 30.0
    assert appended_event.author == "user"
    assert appended_event.branch is None


@pytest.mark.asyncio
async def test_compaction_event_carries_branch():
    long_text = "x" * 5000
    events = [
        _text_event("user", long_text, 1.0, branch="b1"),
        _text_event("agent", long_text, 2.0, branch="b1"),
        _text_event("user", long_text, 3.0, branch="b1"),
    ]
    inv_ctx = _make_invocation_context(events, branch="b1")

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

    appended_event = inv_ctx.session_service.append_event.call_args.kwargs["event"]
    assert appended_event.branch == "b1"


# ---------------------------------------------------------------------------
# Tests: pinned content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pinned_content_embedded_in_compaction():
    long_text = "x" * 5000
    events = [
        _text_event("user", long_text, 1.0),
        _text_event("agent", long_text, 2.0),
        _text_event("user", long_text, 3.0),
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
            return_value="password: secret123\nurl: https://ctf.example.com",
        ),
        patch(
            "opensage.memory.file_based.short_term.persist_traj_json_for_invocation",
            new_callable=AsyncMock,
        ),
    ):
        await history_compaction_on_event(inv_ctx, events[-1])

    appended = inv_ctx.session_service.append_event.call_args.kwargs["event"]
    all_text = "\n".join(
        p.text for p in appended.actions.compaction.compacted_content.parts if p.text
    )
    assert "password: secret123" in all_text
    assert "[[PINNED_CONTEXT" in all_text
    assert "[[/PINNED_CONTEXT]]" in all_text


@pytest.mark.asyncio
async def test_pinned_read_failure_does_not_block_compaction():
    long_text = "x" * 5000
    events = [
        _text_event("user", long_text, 1.0),
        _text_event("agent", long_text, 2.0),
        _text_event("user", long_text, 3.0),
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
            side_effect=Exception("sandbox down"),
        ),
        patch(
            "opensage.memory.file_based.short_term.persist_traj_json_for_invocation",
            new_callable=AsyncMock,
        ),
    ):
        await history_compaction_on_event(inv_ctx, events[-1])

    inv_ctx.session_service.append_event.assert_awaited_once()
    appended = inv_ctx.session_service.append_event.call_args.kwargs["event"]
    all_text = "\n".join(
        p.text for p in appended.actions.compaction.compacted_content.parts if p.text
    )
    assert "SUM" in all_text
    assert "PINNED_CONTEXT" not in all_text


# ---------------------------------------------------------------------------
# Tests: summarizer returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_compaction_when_summarizer_returns_none():
    long_text = "x" * 5000
    events = [
        _text_event("user", long_text, 1.0),
        _text_event("agent", long_text, 2.0),
        _text_event("user", long_text, 3.0),
    ]
    inv_ctx = _make_invocation_context(events)

    fake_summarizer = AsyncMock()
    fake_summarizer.maybe_summarize_events = AsyncMock(return_value=None)

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
    ):
        result = await history_compaction_on_event(inv_ctx, events[-1])

    assert result is None
    inv_ctx.session_service.append_event.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests: compaction_percent controls window size
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_percent_controls_window():
    long_text = "x" * 5000
    events = [
        _text_event("user", long_text, 1.0),
        _text_event("agent", long_text, 2.0),
        _text_event("user", long_text, 3.0),
        _text_event("agent", long_text, 4.0),
        _text_event("user", long_text, 5.0),
        _text_event("agent", long_text, 6.0),
    ]
    inv_ctx = _make_invocation_context(events)

    fake_summarizer = AsyncMock()
    fake_summarizer.maybe_summarize_events = AsyncMock(
        return_value=_text_content("model", "SUM")
    )

    # pct=50 with 6 candidates => window_size = floor(6*50/100) = 3
    with (
        patch(
            "opensage.features.summarization.get_opensage_session_id_from_context",
            return_value="sid",
        ),
        patch(
            "opensage.session.get_opensage_session",
            return_value=_FakeOpenSageSession(budget=100, pct=50),
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

    call_args = fake_summarizer.maybe_summarize_events.call_args
    window_events = call_args.kwargs["events"]
    assert len(window_events) == 3


# ---------------------------------------------------------------------------
# Tests: persist_traj_json failure doesn't crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_traj_failure_does_not_crash():
    long_text = "x" * 5000
    events = [
        _text_event("user", long_text, 1.0),
        _text_event("agent", long_text, 2.0),
        _text_event("user", long_text, 3.0),
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
            side_effect=Exception("disk full"),
        ),
    ):
        result = await history_compaction_on_event(inv_ctx, events[-1])

    assert result is None
    inv_ctx.session_service.append_event.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: branch filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filters_events_by_branch():
    long_text = "x" * 5000
    events = [
        _text_event("user", long_text, 1.0, branch="b1"),
        _text_event("agent", long_text, 2.0, branch="b2"),  # different branch
        _text_event("user", long_text, 3.0, branch="b1"),
        _text_event("agent", long_text, 4.0, branch="b1"),
    ]
    inv_ctx = _make_invocation_context(events, branch="b1")

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

    call_args = fake_summarizer.maybe_summarize_events.call_args
    window_events = call_args.kwargs["events"]
    # Only b1 events: ts=1.0, 3.0, 4.0
    assert len(window_events) == 3
    for ev in window_events:
        assert ev.branch == "b1"
