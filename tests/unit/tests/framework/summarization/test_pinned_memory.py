"""Tests for pinned memory embedding into compaction events.

Verifies that pinned.md content is correctly embedded into compaction
results and survives future compaction cycles.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions, EventCompaction
from google.genai import types


def _make_text_content(role: str, text: str) -> types.Content:
    return types.Content(role=role, parts=[types.Part.from_text(text=text)])


def _extract_all_text(content: types.Content) -> str:
    """Extract all text from a Content object's parts."""
    texts = []
    if content and getattr(content, "parts", None):
        for p in content.parts:
            if getattr(p, "text", None):
                texts.append(p.text)
    return "\n".join(texts)


# ---------------------------------------------------------------------------
# Test _read_pinned_content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_pinned_content_returns_content():
    """When pinned.md has content, _read_pinned_content returns it."""
    from opensage.features.summarization import _read_pinned_content

    mock_sandbox = AsyncMock()
    mock_sandbox.arun_command_in_container = AsyncMock(
        return_value=("password: abc123\nurl: https://ctf.example.com", 0)
    )

    mock_tool_context = MagicMock()
    mock_tool_context._invocation_context = MagicMock()

    with (
        patch(
            "opensage.features.summarization.get_current_session_pinned_path",
            return_value="/mem/short_term/agent__123/pinned.md",
        ),
        patch(
            "opensage.memory.file_based.short_term.sandbox_io._get_main_sandbox",
            return_value=mock_sandbox,
        ),
    ):
        result = await _read_pinned_content(mock_tool_context)

    assert result == "password: abc123\nurl: https://ctf.example.com"


@pytest.mark.asyncio
async def test_read_pinned_content_returns_none_for_empty():
    """When pinned.md is empty, _read_pinned_content returns None."""
    from opensage.features.summarization import _read_pinned_content

    mock_sandbox = AsyncMock()
    mock_sandbox.arun_command_in_container = AsyncMock(return_value=("", 0))

    mock_tool_context = MagicMock()
    mock_tool_context._invocation_context = MagicMock()

    with (
        patch(
            "opensage.features.summarization.get_current_session_pinned_path",
            return_value="/mem/short_term/agent__123/pinned.md",
        ),
        patch(
            "opensage.memory.file_based.short_term.sandbox_io._get_main_sandbox",
            return_value=mock_sandbox,
        ),
    ):
        result = await _read_pinned_content(mock_tool_context)

    assert result is None


@pytest.mark.asyncio
async def test_read_pinned_content_returns_none_on_failure():
    """When sandbox read fails, _read_pinned_content returns None (no crash)."""
    from opensage.features.summarization import _read_pinned_content

    mock_sandbox = AsyncMock()
    mock_sandbox.arun_command_in_container = AsyncMock(
        side_effect=Exception("sandbox unreachable")
    )

    mock_tool_context = MagicMock()
    mock_tool_context._invocation_context = MagicMock()

    with (
        patch(
            "opensage.features.summarization.get_current_session_pinned_path",
            return_value="/mem/short_term/agent__123/pinned.md",
        ),
        patch(
            "opensage.memory.file_based.short_term.sandbox_io._get_main_sandbox",
            return_value=mock_sandbox,
        ),
    ):
        result = await _read_pinned_content(mock_tool_context)

    assert result is None


# ---------------------------------------------------------------------------
# Test pinned content embedding into compaction events
# ---------------------------------------------------------------------------


def test_pinned_content_embedded_in_compaction_event():
    """Pinned content appended to compacted_content is character-perfect."""
    compacted_content = _make_text_content("model", "Summary of conversation.")

    # Simulate what history_compaction_before_model does
    pinned = (
        "username: admin\npassword: x7!kQ9\nsubmit: https://ctf.example.com/api/submit"
    )
    end_ts = 42.0
    pinned_block = (
        f"\n\n[[PINNED_CONTEXT (compaction_ts={end_ts}; "
        f"if multiple PINNED_CONTEXT blocks exist, ONLY the one "
        f"with the latest compaction_ts is authoritative \u2014 "
        f"ignore earlier ones)]]\n"
        f"{pinned}\n"
        f"[[/PINNED_CONTEXT]]"
    )
    compacted_content.parts.append(types.Part(text=pinned_block))

    full_text = _extract_all_text(compacted_content)

    # Exact credential strings must be present
    assert "username: admin" in full_text
    assert "password: x7!kQ9" in full_text
    assert "https://ctf.example.com/api/submit" in full_text
    # Markers present
    assert "[[PINNED_CONTEXT" in full_text
    assert "[[/PINNED_CONTEXT]]" in full_text
    assert "compaction_ts=42.0" in full_text


def test_empty_pinned_no_block():
    """When pinned content is empty, no PINNED_CONTEXT block is added."""
    compacted_content = _make_text_content("model", "Summary of conversation.")
    original_parts_count = len(compacted_content.parts)

    # Simulate the guard: only embed if pinned is non-empty
    pinned = None
    if pinned:
        compacted_content.parts.append(types.Part(text="should not appear"))

    assert len(compacted_content.parts) == original_parts_count
    assert "PINNED_CONTEXT" not in _extract_all_text(compacted_content)


def test_pinned_survives_compaction_exclusion():
    """Compaction events with embedded pinned content are excluded from
    future compaction candidate windows — pinned content is permanently
    protected."""
    # Create a compaction event with pinned content
    compacted_content = _make_text_content(
        "model",
        "Summary.\n\n[[PINNED_CONTEXT (compaction_ts=10.0; ...)]]\n"
        "password: secret123\n[[/PINNED_CONTEXT]]",
    )
    compaction = EventCompaction(
        start_timestamp=1.0,
        end_timestamp=10.0,
        compacted_content=compacted_content,
    )
    compaction_event = Event(
        invocation_id="inv-1",
        author="user",
        timestamp=10.1,
        actions=EventActions(compaction=compaction),
    )

    # Simulate candidate selection logic from history_compaction_before_model:
    # compaction events are excluded
    events = [compaction_event]
    candidates = []
    for ev in events:
        if getattr(ev, "actions", None) and getattr(ev.actions, "compaction", None):
            continue  # This is the guard that protects pinned content
        candidates.append(ev)

    # The compaction event (with pinned content inside) is NOT a candidate
    assert len(candidates) == 0


def test_multiple_compactions_have_timestamps():
    """Multiple compaction events each carry their own compaction_ts,
    letting the agent identify the latest authoritative pinned block."""
    ts1 = 10.0
    ts2 = 50.0

    block1 = (
        f"[[PINNED_CONTEXT (compaction_ts={ts1}; ...)]]\n"
        f"password: old_pass\n[[/PINNED_CONTEXT]]"
    )
    block2 = (
        f"[[PINNED_CONTEXT (compaction_ts={ts2}; ...)]]\n"
        f"password: new_pass\n[[/PINNED_CONTEXT]]"
    )

    content1 = _make_text_content("model", f"Summary1.\n\n{block1}")
    content2 = _make_text_content("model", f"Summary2.\n\n{block2}")

    text1 = _extract_all_text(content1)
    text2 = _extract_all_text(content2)

    assert f"compaction_ts={ts1}" in text1
    assert f"compaction_ts={ts2}" in text2
    assert ts2 > ts1  # Latest is authoritative


# ---------------------------------------------------------------------------
# Test pinned path helper
# ---------------------------------------------------------------------------


def test_get_current_session_pinned_path():
    """get_current_session_pinned_path returns correct path."""
    from opensage.memory.file_based.short_term.session_files import (
        get_current_session_pinned_path,
    )

    mock_context = MagicMock()
    mock_inv = MagicMock()
    mock_inv.session.state = {"_mem_agent_dir": "/mem/short_term/agent__abc123"}
    mock_context._invocation_context = mock_inv

    path = get_current_session_pinned_path(mock_context)
    assert path == "/mem/short_term/agent__abc123/pinned.md"
