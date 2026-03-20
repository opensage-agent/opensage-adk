from __future__ import annotations

from typing import Any, Dict, Optional

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


async def post_to_board(
    message: str,
    tool_context: ToolContext,
    kind: str = "note",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Post a message to the message board (ensemble-only).

    This is used for parallel sub-agent coordination during ensemble runs.
    Outside an ensemble run, this tool is unavailable and will return an error.
    """
    session_id = get_opensage_session_id_from_context(tool_context)
    session = get_opensage_session(session_id)
    agent_id = _resolve_agent_instance_id(tool_context)
    board_id = _resolve_board_id(tool_context)
    if not board_id:
        return {
            "success": False,
            "posted": False,
            "error": (
                "post_to_board is only available during ensemble runs "
                "(missing message board id in context)"
            ),
        }
    board = (
        session.get_message_board(board_id=board_id)
        if hasattr(session, "get_message_board")
        else None
    )
    if board is None:
        return {
            "success": False,
            "posted": False,
            "error": "Session does not support message boards",
        }
    await board.append(agent_id=agent_id, kind=kind, text=message, metadata=metadata)
    return {
        "success": True,
        "posted": True,
        "kind": kind,
    }
