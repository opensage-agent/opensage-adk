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

        # Tool results from any OpenSageAgent are guaranteed to be dicts because
        # ``OpenSageAgent.__init__`` runs ``make_toollikes_safe_dict`` over every
        # tool, which wraps non-dict returns into ``{"result": value}`` before
        # the runner ever sees them. So in practice ``result`` is always a dict
        # here. Anything else means a tool slipped past normalization (third-
        # party plugin / direct injection) — log and skip rather than silently
        # changing its return shape.
        if isinstance(result, dict):
            result["_incoming_messages"] = formatted
            return None  # mutate in place, don't override
        # Defensive fallback: cursor was already advanced by pop_all so these
        # messages would be silently dropped. Log loudly to surface the bug
        # and let the caller fix the broken normalization.
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
