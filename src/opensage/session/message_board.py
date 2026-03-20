"""Session-scoped message board for parallel sub-agent communication."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class MessageBoardPaths:
    board_path: Path
    lock_path: Path


class MessageBoardManager:
    """Append-only JSONL board with per-agent read cursors."""

    def __init__(
        self,
        *,
        base_dir: Path,
        session_id: str,
        board_id: str | None = None,
        lock_ttl_seconds: int = 60,
    ):
        self._session_id = session_id
        self._board_id = board_id
        self._lock_ttl_seconds = int(lock_ttl_seconds)
        if board_id:
            root = (
                Path(base_dir)
                / "opensage_message_board"
                / session_id
                / "boards"
                / board_id
            )
        else:
            root = Path(base_dir) / "opensage_message_board" / session_id
        self._paths = MessageBoardPaths(
            board_path=root / "board.jsonl",
            lock_path=root / "board.lock",
        )
        self._paths.board_path.parent.mkdir(parents=True, exist_ok=True)
        self._offset_by_agent: dict[str, int] = {}

        # Ensure board exists.
        if not self._paths.board_path.exists():
            self._paths.board_path.write_text("", encoding="utf-8")

    @property
    def paths(self) -> MessageBoardPaths:
        return self._paths

    @property
    def board_id(self) -> str | None:
        return self._board_id

    def _now_ts(self) -> float:
        return time.time()

    async def _acquire_lock(self, *, owner: str) -> None:
        backoff = 0.05
        while True:
            try:
                fd = os.open(
                    str(self._paths.lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644,
                )
                try:
                    os.write(
                        fd,
                        json.dumps(
                            {"owner": owner, "ts": self._now_ts()}, ensure_ascii=False
                        ).encode("utf-8"),
                    )
                finally:
                    os.close(fd)
                return
            except FileExistsError:
                # Dead lockfile recovery by TTL.
                try:
                    st = self._paths.lock_path.stat()
                    age = self._now_ts() - float(st.st_mtime)
                    if age > self._lock_ttl_seconds:
                        try:
                            self._paths.lock_path.unlink()
                        except FileNotFoundError:
                            pass
                except FileNotFoundError:
                    pass
                await asyncio.sleep(backoff)
                backoff = min(1.0, backoff * 1.5)

    def _release_lock(self) -> None:
        try:
            self._paths.lock_path.unlink()
        except FileNotFoundError:
            pass

    async def append(
        self,
        *,
        agent_id: str,
        kind: str,
        text: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        owner = f"{self._session_id}:{agent_id}"
        await self._acquire_lock(owner=owner)
        try:
            record: dict[str, Any] = {
                "ts": self._now_ts(),
                "session_id": self._session_id,
                "agent_id": agent_id,
                "kind": kind,
                "text": text,
            }
            if metadata:
                record["metadata"] = metadata
            line = json.dumps(record, ensure_ascii=False)
            # Append atomically under lock.
            with self._paths.board_path.open("a", encoding="utf-8") as f:
                f.write(line)
                f.write("\n")
                f.flush()
        finally:
            self._release_lock()

    async def read_diff(
        self,
        *,
        agent_id: str,
        max_bytes: int = 32_000,
    ) -> str:
        """Read unread board tail for agent_id and advance cursor."""
        max_bytes = int(max_bytes)
        start = int(self._offset_by_agent.get(agent_id, 0))
        try:
            size = self._paths.board_path.stat().st_size
        except FileNotFoundError:
            return ""
        if start >= size:
            return ""

        to_read = min(max_bytes, size - start)

        # Reading can block a bit; run in a thread to avoid stalling the event loop.
        def _read_chunk() -> tuple[str, int]:
            with self._paths.board_path.open("rb") as f:
                f.seek(start)
                data = f.read(to_read)
            if not data:
                return "", start
            # Avoid returning a partial trailing line if a writer is mid-append.
            last_nl = data.rfind(b"\n")
            if last_nl == -1:
                # No newline observed; keep cursor unchanged and try later.
                return "", start
            complete = data[: last_nl + 1]
            new_offset = start + len(complete)
            text = complete.decode("utf-8", errors="replace")
            return text, new_offset

        chunk, new_offset = await asyncio.to_thread(_read_chunk)
        self._offset_by_agent[agent_id] = new_offset
        return chunk.strip()

    def cleanup(self) -> None:
        """Best-effort cleanup of board files.

        For non-default boards (board_id is set), this removes the entire board
        directory. For the default session board, callers should avoid cleanup.
        """
        if not self._board_id:
            return
        root = self._paths.board_path.parent
        try:
            # Avoid importing shutil globally for hot paths.
            import shutil  # pylint: disable=g-import-not-at-top

            shutil.rmtree(root, ignore_errors=True)
        except Exception:
            # Cleanup must never be fatal; ignore best-effort failures.
            pass


_MESSAGE_BOARD_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "opensage_message_board_id", default=None
)


def get_current_message_board_id() -> str | None:
    """Return the current message board id for this async context."""
    return _MESSAGE_BOARD_ID.get()


@contextlib.contextmanager
def message_board_context(board_id: str | None):
    """Bind a message board id to the current async context."""
    token = _MESSAGE_BOARD_ID.set(board_id)
    try:
        yield
    finally:
        _MESSAGE_BOARD_ID.reset(token)
