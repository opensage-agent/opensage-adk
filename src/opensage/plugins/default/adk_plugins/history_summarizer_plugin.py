from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from google.adk.plugins.base_plugin import BasePlugin

from opensage.features import summarization

if TYPE_CHECKING:
    from google.adk.agents.callback_context import CallbackContext
    from google.adk.models.llm_request import LlmRequest
    from google.adk.models.llm_response import LlmResponse


class HistorySummarizerPlugin(BasePlugin):
    """Runs history compaction once before each LLM call.

    At this point all tool responses from the previous round are already
    appended to the session, so the budget check sees the true context size.
    """

    def __init__(self) -> None:
        super().__init__(name="history_summarizer")

    async def before_model_callback(
        self,
        *,
        callback_context: "CallbackContext",
        llm_request: "LlmRequest",
    ) -> Optional["LlmResponse"]:
        await summarization.history_compaction_before_model(
            callback_context, llm_request
        )
        return None
