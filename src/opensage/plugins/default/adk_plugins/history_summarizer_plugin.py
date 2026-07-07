from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from google.adk.plugins.base_plugin import BasePlugin

from opensage.features import summarization

if TYPE_CHECKING:
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.events.event import Event


class HistorySummarizerPlugin(BasePlugin):
    """Runs history compaction after each event is appended to the session.

    Compaction in ``on_event_callback`` modifies ``session.events`` before
    ``_ContentLlmRequestProcessor`` builds ``llm_request.contents``, so the
    next LLM call sees already-compacted history — no timing gap.
    """

    def __init__(self) -> None:
        super().__init__(name="history_summarizer")

    async def on_event_callback(
        self,
        *,
        invocation_context: "InvocationContext",
        event: "Event",
    ) -> Optional["Event"]:
        await summarization.history_compaction_on_event(invocation_context, event)
        return None
