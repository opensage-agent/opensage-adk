"""Server-side web search tool for LLM providers that support it.

Works with Anthropic, OpenAI, xAI, and other providers via litellm's unified
``web_search_options`` parameter.

The tool itself performs no local execution — it injects configuration into
the outgoing LLM request so the provider's server executes the search during
generation.  This mirrors how ADK's ``GoogleSearchTool`` works for Gemini.

Requires the ``litellm_web_search`` patch to be applied (see
``opensage.patches.litellm_web_search``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from google.adk.tools.base_tool import BaseTool
from typing_extensions import override

if TYPE_CHECKING:
    from google.adk.models.llm_request import LlmRequest
    from google.adk.tools.tool_context import ToolContext


class WebSearchTool(BaseTool):
    """A server-side web search tool for litellm-backed providers.

    When added to an agent's tool list, the tool injects
    ``web_search_options`` into the litellm completion call via a custom
    attribute on the ``LlmRequest``.  The actual search is performed
    server-side by the LLM provider.

    Example::

        from opensage.toolbox.general.web_search_tool import WebSearchTool

        agent = OpenSageAgent(
            model=LiteLlm(model="anthropic/claude-sonnet-4-6"),
            tools=[bash_tool_main, WebSearchTool(search_context_size="medium")],
        )
    """

    def __init__(
        self,
        *,
        search_context_size: str = "medium",
        user_location: Optional[Dict[str, str]] = None,
    ):
        """Initialise the web search tool.

        Args:
            search_context_size: Controls how much search context to retrieve.
                One of ``"low"``, ``"medium"``, ``"high"``.  Litellm maps
                these to Anthropic's ``max_uses`` (low=1, medium=5, high=10).
            user_location: Optional location dict for localised results.
                Keys: ``type`` (must be ``"approximate"``), ``city``,
                ``region``, ``country``, ``timezone``.
        """
        super().__init__(name="web_search", description="web_search")
        self._options: Dict[str, Any] = {
            "search_context_size": search_context_size,
        }
        if user_location is not None:
            self._options["user_location"] = user_location

    @override
    async def process_llm_request(
        self,
        *,
        tool_context: ToolContext,
        llm_request: LlmRequest,
    ) -> None:
        """Inject ``web_search_options`` into the LLM request.

        The patched ``LiteLlm.generate_content_async`` picks up
        ``_extra_completion_kwargs`` and merges it into the litellm call.
        """
        extra: Dict[str, Any] = getattr(llm_request, "_extra_completion_kwargs", {})
        extra["web_search_options"] = dict(self._options)
        llm_request._extra_completion_kwargs = extra  # type: ignore[attr-defined]


web_search = WebSearchTool()
