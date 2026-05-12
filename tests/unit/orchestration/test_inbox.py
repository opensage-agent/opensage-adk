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
