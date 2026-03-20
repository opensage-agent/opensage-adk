from __future__ import annotations

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from opensage.features import summarization


class QuotaAfterToolPlugin(BasePlugin):
    """Plugin wrapper around quota_after_tool_callback."""

    def __init__(self) -> None:
        super().__init__(name="quota_after_tool")

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict,
        tool_context: ToolContext,
        result: dict,
    ):
        return await summarization.quota_after_tool_callback(
            tool, tool_args, tool_context, result
        )
