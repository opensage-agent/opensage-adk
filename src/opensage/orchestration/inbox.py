"""File-backed JSONL inbox for peer-to-peer messaging."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from .types import Message

logger = logging.getLogger("opensage." + __name__)


class Inbox:
    """A per-instance file-backed inbox.

    Stores peer messages (sent via ``send_message``) as JSON Lines. The cursor
    (already-read byte offset) is kept in-memory only — instances are never
    unloaded, so there is no need to persist the cursor to disk.

    Inbox stores ONLY peer messages. Requests delivered via ``call_subagent`` or
    ``continue_agent_instance`` bypass the inbox.
    """

    def __init__(self, jsonl_path: Path) -> None:
        self._path = Path(jsonl_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.touch()
        self._lock = asyncio.Lock()
        self._cursor: int = 0

    # ---- static push (no instance required) ----

    @staticmethod
    async def append_to(path: Path, message: Message) -> None:
        """Append a message to an inbox file without constructing an Inbox object.

        Used by AgentManager.send_message when the target instance may not be
        loaded into memory. File-level atomicity is provided by ``O_APPEND``; we
        do not acquire a lock here because each append writes a single complete
        line.
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(message.to_dict(), ensure_ascii=False)
        # Use asyncio.to_thread so file I/O does not block the event loop.
        await asyncio.to_thread(_append_line, p, line)

    # ---- instance methods ----

    async def push(self, message: Message) -> None:
        """Append a message to the inbox (with our in-process lock)."""
        async with self._lock:
            await Inbox.append_to(self._path, message)

    async def pop_all(self) -> list[Message]:
        """Read all unread messages and advance the in-memory cursor to EOF."""
        async with self._lock:
            data, new_cursor = await asyncio.to_thread(
                _read_from, self._path, self._cursor
            )
            if not data:
                return []
            messages: list[Message] = []
            for raw in data.splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                    messages.append(Message.from_dict(obj))
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(
                        "Skipping malformed inbox line: %s (%s)", raw[:120], e
                    )
            self._cursor = new_cursor
            return messages

    async def has_pending(self) -> bool:
        """Return True if file size > cursor (advisory, no lock)."""
        try:
            size = await asyncio.to_thread(os.path.getsize, self._path)
            return size > self._cursor
        except FileNotFoundError:
            return False

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def path(self) -> Path:
        return self._path


def _append_line(path: Path, line: str) -> None:
    """Synchronous helper for appending a line (runs in a thread)."""
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.write("\n")


def _read_from(path: Path, start: int) -> tuple[str, int]:
    """Synchronous helper: read from ``start`` to EOF. Returns (data, new_cursor)."""
    try:
        size = os.path.getsize(path)
    except FileNotFoundError:
        return "", start
    if size <= start:
        return "", start
    with path.open("r", encoding="utf-8") as f:
        f.seek(start)
        data = f.read()
    return data, size
