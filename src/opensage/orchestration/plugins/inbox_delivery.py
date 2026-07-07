"""ADK Plugin that delivers inbox messages at tool boundaries.

When an agent is RUNNING and peer messages are pushed to its inbox, this plugin
pops them on each ``after_tool_callback`` and attaches them to the tool's return
value via the ``_incoming_messages`` key. The LLM sees these messages in the
next turn alongside the tool result.

Plugin is Runner-scoped (one plugin instance per AgentInstance's Runner), so it
does NOT mutate the shared OpenSageAgent object.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from google.adk.plugins.base_plugin import BasePlugin

if TYPE_CHECKING:
    from google.adk.tools.base_tool import BaseTool
    from google.adk.tools.tool_context import ToolContext

    from opensage.orchestration.inbox import Inbox

logger = logging.getLogger("opensage." + __name__)

_MAX_INCOMING_MESSAGES_CHARS = 5000


def _truncate_incoming_messages(
    formatted: str, limit: int, saved_path: str | None
) -> str:
    if len(formatted) <= limit:
        return formatted
    header_end = formatted.find("\n\n")
    if header_end == -1:
        header_end = 0
    file_note = ""
    if saved_path:
        file_note = f"\nFull incoming messages saved to: {saved_path}\n"
    header = formatted[: header_end + 2] if header_end else ""
    overhead = 150 + len(file_note)
    remaining = limit - len(header) - overhead
    if remaining < 200:
        return formatted[:limit]
    truncated = (
        formatted[: header_end + 2 + remaining] if header_end else formatted[:remaining]
    )
    truncated += (
        f"\n\n... [truncated: {len(formatted)} total chars, "
        f"showing first {limit} — full messages were too large for context]"
        f"{file_note}"
    )
    return truncated


async def _save_full_messages(tool_context: "ToolContext", content: str) -> str | None:
    """Save the full incoming messages to a file in the sandbox, return the path."""
    try:
        from opensage.memory.file_based.short_term import (
            get_current_session_tool_outputs_dir,
        )
        from opensage.utils.agent_utils import save_content_to_sandbox_file

        output_dir = get_current_session_tool_outputs_dir(tool_context)
        return await save_content_to_sandbox_file(
            context=tool_context,
            content=content,
            tool_name="incoming_messages",
            output_dir=output_dir,
            file_id=tool_context.function_call_id,
            file_extension=".log",
        )
    except Exception:
        logger.exception("InboxDeliveryPlugin: failed to save full incoming messages")
        return None


class InboxDeliveryPlugin(BasePlugin):
    """Delivers pending inbox messages on every tool boundary."""

    def __init__(self, inbox: "Inbox") -> None:
        super().__init__(name="inbox_delivery")
        self._inbox = inbox

    async def after_tool_callback(
        self,
        *,
        tool: "BaseTool",
        tool_args: dict[str, Any],
        tool_context: "ToolContext",
        result: Any,
    ) -> Optional[dict[str, Any]]:
        try:
            if not await self._inbox.has_pending():
                return None
            messages = await self._inbox.pop_all()
        except Exception:
            logger.exception("InboxDeliveryPlugin failed to read inbox")
            return None

        if not messages:
            return None

        from opensage.orchestration.manager import _format_messages_block

        formatted = _format_messages_block(messages)

        if len(formatted) > _MAX_INCOMING_MESSAGES_CHARS:
            saved_path = await _save_full_messages(tool_context, formatted)
            logger.warning(
                "InboxDeliveryPlugin: truncating _incoming_messages from %d to %d chars"
                " (full saved to %s)",
                len(formatted),
                _MAX_INCOMING_MESSAGES_CHARS,
                saved_path,
            )
            formatted = _truncate_incoming_messages(
                formatted, _MAX_INCOMING_MESSAGES_CHARS, saved_path
            )

        if isinstance(result, dict):
            result["_incoming_messages"] = formatted
            return None
        logger.error(
            "InboxDeliveryPlugin: tool %r returned non-dict (%s); %d inbox "
            "message(s) dropped because tool result shape cannot carry them. "
            "This indicates the tool slipped past make_toollikes_safe_dict — "
            "all OpenSageAgent tools should return dict.",
            getattr(tool, "name", "?"),
            type(result).__name__,
            len(messages),
        )
        return None
