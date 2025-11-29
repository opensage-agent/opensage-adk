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
