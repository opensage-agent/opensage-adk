from google.adk.models.google_llm import Gemini
from google.adk.tools.google_search_tool import GoogleSearchTool

from opensage.agents.opensage_agent import OpenSageAgent


def mk_agent(opensage_session_id: str):
    return OpenSageAgent(
        name="google_search_agent",
        model=Gemini(model="gemini-2.5-flash"),
        description="An agent with Google Search grounding.",
        instruction=(
            "You are a helpful assistant with Google Search capability. "
            "Use search to answer questions that need up-to-date information. "
            "Keep answers concise."
        ),
        tools=[GoogleSearchTool()],
    )
