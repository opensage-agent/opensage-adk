from __future__ import annotations

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from opensage.session import get_opensage_session
from opensage.session.message_board import get_current_message_board_id
from opensage.utils.agent_utils import get_opensage_session_id_from_context


def _resolve_agent_instance_id(tool_context: ToolContext) -> str:
    agent = getattr(getattr(tool_context, "_invocation_context", None), "agent", None)
    if agent is None:
        return "unknown_agent"
    instance_id = getattr(agent, "_instance_id", None)
    if isinstance(instance_id, str) and instance_id.strip():
        return instance_id
    name = getattr(agent, "name", None)
    if isinstance(name, str) and name.strip():
        return name
    return "unknown_agent"


def _resolve_board_id(tool_context: ToolContext) -> str | None:
    board_id = get_current_message_board_id()
    if board_id:
        return board_id
    state = getattr(tool_context, "state", None)
    if hasattr(state, "get"):
        return state.get("opensage_message_board_id")
    return None


class MessageBoardDiffPlugin(BasePlugin):
    """Piggyback unread message board diffs onto tool responses.

    Important: This plugin MUST return None to avoid short-circuiting other
    plugins and agent callbacks.
    """

    def __init__(self) -> None:
        super().__init__(name="message_board_diff")

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict,
        tool_context: ToolContext,
        result: dict,
    ):
        if not isinstance(result, dict):
            return None

        session_id = get_opensage_session_id_from_context(tool_context)
        session = get_opensage_session(session_id)
        agent_id = _resolve_agent_instance_id(tool_context)
        board_id = _resolve_board_id(tool_context)
        if not board_id:
            # Ensemble-only: do not piggyback diffs for normal runs.
            return None
        board = (
            session.get_message_board(board_id=board_id)
            if hasattr(session, "get_message_board")
            else None
        )
        if board is None:
            return None
        diff = await board.read_diff(agent_id=agent_id)
        if diff:
            result["_message_board_diff"] = diff
        return None
