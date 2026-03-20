from __future__ import annotations

import types as _types
from pathlib import Path

import pytest

from opensage.plugins.default.adk_plugins.message_board_diff_plugin import (
    MessageBoardDiffPlugin,
)
from opensage.session.message_board import MessageBoardManager, message_board_context


class _DummyAgent:
    def __init__(self, name: str, instance_id: str | None = None):
        self.name = name
        if instance_id is not None:
            self._instance_id = instance_id


class _DummyInvocationContext:
    def __init__(self, agent: _DummyAgent, session):
        self.agent = agent
        self.session = session


class _DummyToolContext:
    def __init__(self, inv_ctx):
        self._invocation_context = inv_ctx
        self.state = {}


class _DummySession:
    def __init__(self, sid: str, board: MessageBoardManager):
        self.id = sid
        self.state = {"opensage_session_id": sid}
        self._boards = {"b1": board}

    def get_message_board(self, *, board_id=None):
        return self._boards[board_id]


@pytest.mark.asyncio
async def test_message_board_diff_plugin_piggybacks_diff(tmp_path, monkeypatch):
    sid = "sid"
    board = MessageBoardManager(base_dir=Path(tmp_path), session_id=sid, board_id="b1")
    session = _DummySession(sid, board)

    agent_a = _DummyAgent("a", instance_id="a__1")
    agent_b = _DummyAgent("b", instance_id="b__1")

    inv_a = _DummyInvocationContext(agent_a, session)
    inv_b = _DummyInvocationContext(agent_b, session)

    tc_a = _DummyToolContext(inv_a)
    tc_b = _DummyToolContext(inv_b)

    # Patch session lookup helper used by the plugin.
    import opensage.plugins.default.adk_plugins.message_board_diff_plugin as plugin_mod

    monkeypatch.setattr(plugin_mod, "get_opensage_session", lambda _sid: session)
    monkeypatch.setattr(
        plugin_mod, "get_opensage_session_id_from_context", lambda _tc: sid
    )

    await board.append(agent_id="a__1", kind="note", text="hello from a")

    plugin = MessageBoardDiffPlugin()
    result = {"success": True, "result": "ok"}
    with message_board_context("b1"):
        await plugin.after_tool_callback(
            tool=_types.SimpleNamespace(name="dummy"),
            tool_args={},
            tool_context=tc_b,
            result=result,
        )
    assert "_message_board_diff" in result
    assert "hello from a" in result["_message_board_diff"]

    # Cursor advanced: second call should not repeat old diff.
    result2 = {"success": True, "result": "ok"}
    with message_board_context("b1"):
        await plugin.after_tool_callback(
            tool=_types.SimpleNamespace(name="dummy"),
            tool_args={},
            tool_context=tc_b,
            result=result2,
        )
    assert "_message_board_diff" not in result2


@pytest.mark.asyncio
async def test_message_board_diff_plugin_uses_context_board_id(tmp_path, monkeypatch):
    sid = "sid"
    default_board = MessageBoardManager(base_dir=Path(tmp_path), session_id=sid)
    temp_board = MessageBoardManager(
        base_dir=Path(tmp_path), session_id=sid, board_id="tmp_board"
    )

    class _SessionWithBoards(_DummySession):
        def __init__(self, sid: str):
            super().__init__(sid, default_board)
            self._boards = {"b1": default_board, "tmp_board": temp_board}

        def get_message_board(self, *, board_id=None):
            return self._boards[board_id]

    session = _SessionWithBoards(sid)

    agent_a = _DummyAgent("a", instance_id="a__1")
    agent_b = _DummyAgent("b", instance_id="b__1")
    inv_b = _DummyInvocationContext(agent_b, session)
    tc_b = _DummyToolContext(inv_b)

    import opensage.plugins.default.adk_plugins.message_board_diff_plugin as plugin_mod

    monkeypatch.setattr(plugin_mod, "get_opensage_session", lambda _sid: session)
    monkeypatch.setattr(
        plugin_mod, "get_opensage_session_id_from_context", lambda _tc: sid
    )

    # Write to temp board only; without context there should be no diff.
    await temp_board.append(agent_id="a__1", kind="note", text="hello from tmp")

    plugin = MessageBoardDiffPlugin()

    result = {"success": True}
    await plugin.after_tool_callback(
        tool=_types.SimpleNamespace(name="dummy"),
        tool_args={},
        tool_context=tc_b,
        result=result,
    )
    assert "_message_board_diff" not in result

    # With a board_id bound in context, diff should appear.
    result2 = {"success": True}
    with message_board_context("tmp_board"):
        await plugin.after_tool_callback(
            tool=_types.SimpleNamespace(name="dummy"),
            tool_args={},
            tool_context=tc_b,
            result=result2,
        )
    assert "_message_board_diff" in result2
    assert "hello from tmp" in result2["_message_board_diff"]
