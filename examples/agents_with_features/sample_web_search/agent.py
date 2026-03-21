from google.adk.models.lite_llm import LiteLlm

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.general.web_search_tool import WebSearchTool


def mk_agent(opensage_session_id: str):
    return OpenSageAgent(
        name="web_search_agent",
        model=LiteLlm(model="anthropic/claude-sonnet-4-6"),
        description="An agent with server-side web search.",
        instruction=(
            "You are a helpful assistant with web search capability. "
            "Use web search to answer questions that need up-to-date information. "
            "Keep answers concise."
        ),
        tools=[WebSearchTool(search_context_size="medium")],
    )
