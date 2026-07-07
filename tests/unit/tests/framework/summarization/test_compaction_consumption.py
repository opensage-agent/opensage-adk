from __future__ import annotations

import pytest
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions, EventCompaction
from google.genai import types

# Test the ADK consumption-side compaction folding logic:
# After a compaction marker covering [start, end], the LLM request history
# should include the compacted summary and exclude original events within the window.


def _make_text_content(role: str, text: str) -> types.Content:
    return types.Content(role=role, parts=[types.Part.from_text(text=text)])


@pytest.mark.asyncio
async def test_compaction_consumption_replaces_original_window():
    # Invocation 1 original events
    ev1 = Event(
        invocation_id="inv-1",
        author="user",
        timestamp=1.0,
        content=_make_text_content("user", "HELLO_1"),
    )
    ev2 = Event(
        invocation_id="inv-1",
        author="agent",
        timestamp=2.0,
        content=_make_text_content("model", "REPLY_1"),
    )

    # Compaction marker covering [1.0, 2.0], with summary "SUMMARY"
    compaction = EventCompaction(
        start_timestamp=1.0,
        end_timestamp=2.0,
        compacted_content=_make_text_content("model", "SUMMARY"),
    )
    marker = Event(
        invocation_id="inv-1",
        author="user",
        timestamp=2.1,
        actions=EventActions(compaction=compaction),
    )

    # Invocation 2 new user message
    ev3 = Event(
        invocation_id="inv-2",
        author="user",
        timestamp=3.0,
        content=_make_text_content("user", "HELLO_2"),
    )

    events = [ev1, ev2, marker, ev3]

    # Use ADK flow to produce LLM contents (this calls _process_compaction_events internally)
    from google.adk.flows.llm_flows.contents import _get_contents

    contents = _get_contents(current_branch=None, events=events, agent_name="")
    all_texts = []
    for c in contents:
        if c and getattr(c, "parts", None):
            for p in c.parts:
                if getattr(p, "text", None):
                    all_texts.append(p.text)

    joined = "\n".join(all_texts)

    # Expect summary appears, and original window texts are not present anymore
    assert "SUMMARY" in joined
    assert "HELLO_1" not in joined
    assert "REPLY_1" not in joined
    # The later user message should remain
    assert "HELLO_2" in joined


@pytest.mark.asyncio
async def test_later_compaction_subsumes_earlier():
    """When a later compaction's range covers an earlier one, only the later
    summary appears in the folded view (death-spiral prevention)."""
    # Original events
    ev1 = Event(
        invocation_id="inv-1",
        author="user",
        timestamp=1.0,
        content=_make_text_content("user", "HELLO_1"),
    )
    ev2 = Event(
        invocation_id="inv-1",
        author="agent",
        timestamp=2.0,
        content=_make_text_content("model", "REPLY_1"),
    )
    # First compaction covering [1.0, 2.0]
    comp1 = EventCompaction(
        start_timestamp=1.0,
        end_timestamp=2.0,
        compacted_content=_make_text_content("model", "OLD_SUMMARY"),
    )
    marker1 = Event(
        invocation_id="inv-1",
        author="user",
        timestamp=2.1,
        actions=EventActions(compaction=comp1),
    )
    # More events
    ev3 = Event(
        invocation_id="inv-2",
        author="user",
        timestamp=3.0,
        content=_make_text_content("user", "HELLO_2"),
    )
    ev4 = Event(
        invocation_id="inv-2",
        author="agent",
        timestamp=4.0,
        content=_make_text_content("model", "REPLY_2"),
    )
    # Second compaction covering [1.0, 4.0] — subsumes the first
    comp2 = EventCompaction(
        start_timestamp=1.0,
        end_timestamp=4.0,
        compacted_content=_make_text_content("model", "NEW_SUMMARY"),
    )
    marker2 = Event(
        invocation_id="inv-2",
        author="user",
        timestamp=4.1,
        actions=EventActions(compaction=comp2),
    )
    # Tail event
    ev5 = Event(
        invocation_id="inv-3",
        author="user",
        timestamp=5.0,
        content=_make_text_content("user", "HELLO_3"),
    )

    events = [ev1, ev2, marker1, ev3, ev4, marker2, ev5]

    from google.adk.flows.llm_flows.contents import _get_contents

    contents = _get_contents(current_branch=None, events=events, agent_name="")
    all_texts = []
    for c in contents:
        if c and getattr(c, "parts", None):
            for p in c.parts:
                if getattr(p, "text", None):
                    all_texts.append(p.text)

    joined = "\n".join(all_texts)

    # Only the newer summary should appear
    assert "NEW_SUMMARY" in joined
    assert "OLD_SUMMARY" not in joined
    # Original events within the range should be gone
    assert "HELLO_1" not in joined
    assert "REPLY_1" not in joined
    assert "HELLO_2" not in joined
    assert "REPLY_2" not in joined
    # Tail event survives
    assert "HELLO_3" in joined


@pytest.mark.asyncio
async def test_five_compactions_only_latest_survives():
    """Simulates 5 sequential compactions where each subsumes all prior ones.
    Only the latest summary should appear in the folded view."""
    events = []
    # Create 10 original events (ts 1.0 through 10.0)
    for t in range(1, 11):
        events.append(
            Event(
                invocation_id=f"inv-{t}",
                author="user" if t % 2 else "agent",
                timestamp=float(t),
                content=_make_text_content("user" if t % 2 else "model", f"MSG_{t}"),
            )
        )

    # 5 compactions, each expanding to cover all prior ranges
    for i in range(5):
        end_ts = 2.0 * (i + 1)  # covers 2, 4, 6, 8, 10
        comp = EventCompaction(
            start_timestamp=1.0,  # always starts from the beginning
            end_timestamp=end_ts,
            compacted_content=_make_text_content("model", f"SUMMARY_{i + 1}"),
        )
        events.append(
            Event(
                invocation_id=f"inv-comp-{i}",
                author="user",
                timestamp=end_ts + 0.01,
                actions=EventActions(compaction=comp),
            )
        )

    # Add a tail event after all compactions
    events.append(
        Event(
            invocation_id="inv-tail",
            author="user",
            timestamp=11.0,
            content=_make_text_content("user", "TAIL_MSG"),
        )
    )

    from google.adk.flows.llm_flows.contents import _get_contents

    contents = _get_contents(current_branch=None, events=events, agent_name="")
    all_texts = []
    for c in contents:
        if c and getattr(c, "parts", None):
            for p in c.parts:
                if getattr(p, "text", None):
                    all_texts.append(p.text)

    joined = "\n".join(all_texts)

    # Only SUMMARY_5 (the latest, covering [1.0, 10.0]) should survive
    assert "SUMMARY_5" in joined
    for i in range(1, 5):
        assert f"SUMMARY_{i}" not in joined, f"SUMMARY_{i} should be subsumed"
    # Original events within [1.0, 10.0] should be gone
    for t in range(1, 11):
        assert f"MSG_{t}" not in joined
    # Tail survives
    assert "TAIL_MSG" in joined
