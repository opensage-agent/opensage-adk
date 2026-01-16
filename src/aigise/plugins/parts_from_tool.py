from __future__ import annotations

from typing import Any, Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

PARTS_FROM_TOOLS_ID = "temp:PARTS_FROM_TOOLS_ID"


class PartsFromToolPlugin(BasePlugin):
    """A plugin that modifies function tool responses to support returning list of parts directly.

    Should be removed in favor of directly supporting FunctionResponsePart when these
    are supported outside of computer use tool.
    For context see: https://github.com/google/adk-python/issues/3064#issuecomment-3463067459
    """

    def __init__(self, name: str = "parts_from_tool_plugin"):
        """Initialize the parts from tool plugin.

        Args:
          name: The name of the plugin instance.
        """
        super().__init__(name)

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> Optional[LlmResponse]:
        """Attach saved list[google.genai.types.Part] returned by the tool to llm_request."""

        if saved_parts := callback_context.state.get(PARTS_FROM_TOOLS_ID, None):
            llm_request.contents += [types.Content(parts=saved_parts, role="user")]
            callback_context.state.update({PARTS_FROM_TOOLS_ID: []})

        return None
