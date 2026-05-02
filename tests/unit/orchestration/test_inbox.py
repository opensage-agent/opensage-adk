"""Tests for the file-backed Inbox."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from opensage.orchestration.inbox import Inbox
from opensage.orchestration.types import Message


@pytest.mark.asyncio
async def test_push_pop_roundtrip(tmp_path: Path):
    inbox = Inbox(tmp_path / "inbox.jsonl")
    await inbox.push(Message(from_sid="A", to_sid="B", content="hi", kind="text"))
    await inbox.push(Message(from_sid="A", to_sid="B", content="there", kind="text"))

    msgs = await inbox.pop_all()
    assert len(msgs) == 2
    assert msgs[0].content == "hi"
    assert msgs[1].content == "there"
    assert msgs[0].from_sid == "A"

    # second pop returns empty
    assert await inbox.pop_all() == []


@pytest.mark.asyncio
async def test_has_pending(tmp_path: Path):
    inbox = Inbox(tmp_path / "inbox.jsonl")
    assert not await inbox.has_pending()

    await inbox.push(Message(from_sid="A", to_sid="B", content="x"))
    assert await inbox.has_pending()

    await inbox.pop_all()
    assert not await inbox.has_pending()


@pytest.mark.asyncio
async def test_cursor_persistence_across_instances(tmp_path: Path):
    """Cursor must be persisted via flush_cursor and restored on construction."""
    path = tmp_path / "inbox.jsonl"
    inbox1 = Inbox(path)
    await inbox1.push(Message(from_sid="A", to_sid="B", content="one"))
    await inbox1.push(Message(from_sid="A", to_sid="B", content="two"))
    msgs = await inbox1.pop_all()
    assert len(msgs) == 2
    await inbox1.flush_cursor()

    # Brand new Inbox over the same files: cursor is read from .cursor file
    inbox2 = Inbox(path)
    assert not await inbox2.has_pending()
    assert await inbox2.pop_all() == []


@pytest.mark.asyncio
async def test_cursor_not_flushed_without_explicit_call(tmp_path: Path):
    """If we don't flush, a fresh Inbox re-reads from cursor file (still 0)."""
    path = tmp_path / "inbox.jsonl"
    inbox1 = Inbox(path)
    await inbox1.push(Message(from_sid="A", to_sid="B", content="one"))
    await inbox1.pop_all()
    # Do NOT flush_cursor

    inbox2 = Inbox(path)
    # The on-disk cursor is still 0 (or unset), so inbox2 re-reads.
    msgs = await inbox2.pop_all()
    assert len(msgs) == 1  # re-reads the not-yet-flushed data


@pytest.mark.asyncio
async def test_append_to_static(tmp_path: Path):
    """Inbox.append_to works without an Inbox object."""
    path = tmp_path / "inbox.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)

    await Inbox.append_to(path, Message(from_sid="X", to_sid="Y", content="hello"))

    # Later, an Inbox can read it back
    inbox = Inbox(path)
    msgs = await inbox.pop_all()
    assert len(msgs) == 1
    assert msgs[0].content == "hello"
    assert msgs[0].from_sid == "X"


@pytest.mark.asyncio
async def test_concurrent_push_no_corruption(tmp_path: Path):
    inbox = Inbox(tmp_path / "inbox.jsonl")

    async def push_n(n: int):
        for i in range(n):
            await inbox.push(Message(from_sid=f"S{i}", to_sid="B", content=f"msg-{i}"))

    await asyncio.gather(push_n(10), push_n(10), push_n(10))

    msgs = await inbox.pop_all()
    assert len(msgs) == 30
    # All distinct content strings survived
    contents = sorted(m.content for m in msgs)
    assert contents.count("msg-0") == 3
