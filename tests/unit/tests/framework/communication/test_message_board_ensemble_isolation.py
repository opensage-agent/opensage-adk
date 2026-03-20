from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_execute_agent_ensemble_creates_temp_message_board(tmp_path, monkeypatch):
    import opensage.agents.opensage_agent as agent_mod
    import opensage.session.opensage_ensemble_manager as emod
    import opensage.toolbox.tool_normalization as safe_mod
    from opensage.session.message_board import MessageBoardManager
    from opensage.session.opensage_ensemble_manager import (
        EnsembleAgentInfo,
        OpenSageEnsembleManager,
    )

    class DummyOpenSageAgent:
        pass

    monkeypatch.setattr(agent_mod, "OpenSageAgent", DummyOpenSageAgent)

    class DummySession:
        def __init__(self, sid: str):
            self.opensage_session_id = sid
            self.config = SimpleNamespace(
                llm=SimpleNamespace(summarize_model="fake_summarizer_model")
            )
            self._boards = {}

        def get_message_board(self, *, board_id=None):
            if not board_id:
                board = self._boards.get("default")
                if board is None:
                    board = MessageBoardManager(
                        base_dir=tmp_path, session_id=self.opensage_session_id
                    )
                    self._boards["default"] = board
                return board
            board = self._boards.get(board_id)
            if board is None:
                board = MessageBoardManager(
                    base_dir=tmp_path,
                    session_id=self.opensage_session_id,
                    board_id=board_id,
                )
                self._boards[board_id] = board
            return board

        def cleanup_message_board(self, *, board_id: str) -> None:
            board = self._boards.pop(board_id, None)
            if board is not None:
                board.cleanup()

    session = DummySession("sid_test")
    manager = OpenSageEnsembleManager(session)

    # Make sure the isinstance check uses our dummy class.
    monkeypatch.setattr(emod, "OpenSageAgent", DummyOpenSageAgent)

    def _fake_copy_agent_with_updated_model_v2(
        base_agent_info, model_name: str, *, inherit_model=None
    ):
        del inherit_model
        return SimpleNamespace(
            name=f"{base_agent_info.name}_{model_name}",
            tools=[],
        )

    monkeypatch.setattr(
        emod,
        "_copy_agent_with_updated_model_v2",
        _fake_copy_agent_with_updated_model_v2,
    )

    class _DummyTool:
        def __init__(self, name: str):
            self.name = name

    monkeypatch.setattr(
        safe_mod,
        "make_tool_safe_dict",
        lambda f: _DummyTool(getattr(f, "__name__", "tool")),
    )

    seen_board_ids: list[str | None] = []

    class _FakeAgentTool:
        def __init__(self, agent):
            self.agent = agent

        async def run_async(self, *, args, tool_context):
            del args
            from opensage.session.message_board import get_current_message_board_id

            board_id = get_current_message_board_id()
            seen_board_ids.append(board_id)
            board = session.get_message_board(board_id=board_id)
            await board.append(
                agent_id=getattr(tool_context._invocation_context.agent, "name", "a"),
                kind="note",
                text=f"hello {board_id}",
            )
            return {"success": True, "response": "ok"}

    monkeypatch.setattr(emod, "AgentTool", _FakeAgentTool)

    class _FakeModel:
        def __init__(self, model: str):
            self.model = model

        async def generate_content_async(self, request):
            del request
            yield SimpleNamespace(
                content=SimpleNamespace(parts=[SimpleNamespace(text="aggregated")])
            )

    monkeypatch.setattr(emod, "LiteLlm", _FakeModel)

    class _Part:
        @staticmethod
        def from_text(*, text: str):
            return SimpleNamespace(text=text)

    class _Content:
        def __init__(self, *, role: str, parts):
            self.role = role
            self.parts = parts

    monkeypatch.setattr(
        emod,
        "types",
        SimpleNamespace(
            GenerateContentConfig=lambda: None,
            Content=_Content,
            Part=_Part,
        ),
    )

    inv_ctx = SimpleNamespace(
        agent=SimpleNamespace(name="root_agent"),
        session=SimpleNamespace(
            events=[], state={"opensage_session_id": session.opensage_session_id}
        ),
    )
    tool_context = SimpleNamespace(_invocation_context=inv_ctx, state={})

    target_agent_info = EnsembleAgentInfo(
        name="target",
        description="",
        tools=[],
        model="",
        agent_type="dummy",
        agent_instance=DummyOpenSageAgent(),
    )

    await manager.execute_agent_ensemble(
        full_instruction="do it",
        target_agent_info=target_agent_info,
        model_name_to_count={"m": 2},
        current_agent=inv_ctx.agent,
        tool_context=tool_context,
    )

    first_ids = {bid for bid in seen_board_ids if bid is not None}
    assert len(first_ids) == 1
    first_id = next(iter(first_ids))
    assert not (
        tmp_path
        / "opensage_message_board"
        / session.opensage_session_id
        / "boards"
        / first_id
    ).exists()

    # Second run should use a different board id.
    seen_board_ids.clear()
    await manager.execute_agent_ensemble(
        full_instruction="do it again",
        target_agent_info=target_agent_info,
        model_name_to_count={"m": 2},
        current_agent=inv_ctx.agent,
        tool_context=tool_context,
    )
    second_ids = {bid for bid in seen_board_ids if bid is not None}
    assert len(second_ids) == 1
    second_id = next(iter(second_ids))
    assert second_id != first_id
    assert not (
        tmp_path
        / "opensage_message_board"
        / session.opensage_session_id
        / "boards"
        / second_id
    ).exists()
