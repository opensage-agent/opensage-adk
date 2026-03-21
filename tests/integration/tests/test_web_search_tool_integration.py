"""Live integration test for WebSearchTool with a full agent.

Requires ANTHROPIC_API_KEY to be set.  Skipped when the key is absent.

Run:
    ANTHROPIC_API_KEY=sk-... uv run pytest tests/integration/tests/test_web_search_tool_integration.py -v
"""

import os
import uuid
import warnings

import pytest
from google.adk import Runner
from google.adk.apps.app import App
from google.adk.models.lite_llm import LiteLlm
from google.genai import types

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.features.opensage_in_memory_session_service import (
    OpenSageInMemorySessionService,
)
from opensage.toolbox.general.web_search_tool import WebSearchTool

# Filter out Pydantic serialization warnings from LiteLLM
warnings.filterwarnings("ignore", message=".*Pydantic serializer warnings.*")
warnings.filterwarnings("ignore", message=".*PydanticSerializationUnexpectedValue.*")


@pytest.fixture
def web_search_agent():
    """Create a minimal agent with WebSearchTool."""
    from opensage.features.agent_history_tracker import disable_neo4j_logging

    try:
        disable_neo4j_logging()
    except Exception:
        pass

    agent = OpenSageAgent(
        name="web_search_test_agent",
        model=LiteLlm(model="anthropic/claude-sonnet-4-6"),
        description="Test agent with web search.",
        instruction="You MUST use web search for every question. Never answer from memory. Always search first, then answer based on search results. Keep answers short.",
        tools=[WebSearchTool(search_context_size="low")],
    )

    yield agent

    from opensage.session.opensage_session import OpenSageSessionRegistry

    OpenSageSessionRegistry.cleanup_all_sessions()


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_web_search_agent_returns_grounded_response(web_search_agent):
    """Agent with WebSearchTool should use server-side search and return a grounded answer."""
    app_name = "web_search_test"
    user_id = "test_user"
    session_id = str(uuid.uuid4())

    session_service = OpenSageInMemorySessionService()
    app = App(name=app_name, root_agent=web_search_agent)
    runner = Runner(app=app, session_service=session_service)

    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state={"opensage_session_id": session_id},
    )

    events = []
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text="What is the current price of Bitcoin in USD today?"
                )
            ],
        ),
    ):
        events.append(event)

    # Check the text response
    response_texts = []
    for event in events:
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    response_texts.append(part.text)

    full_response = " ".join(response_texts)
    assert len(full_response) > 0, "Expected non-empty response"
    # Response should mention a dollar amount (real-time price)
    assert "$" in full_response or "USD" in full_response, (
        f"Expected price info in response: {full_response[:500]}"
    )

    # Verify grounding metadata was captured from web search
    grounding_events = [e for e in events if e.grounding_metadata is not None]
    assert len(grounding_events) > 0, (
        "Expected at least one event with grounding_metadata from web search"
    )

    gm = grounding_events[0].grounding_metadata
    # Should have captured the search query
    assert gm.web_search_queries, "Expected web_search_queries in grounding_metadata"
    assert len(gm.web_search_queries) > 0

    # Should have captured result URLs
    assert gm.grounding_chunks, "Expected grounding_chunks with search result URLs"
    assert len(gm.grounding_chunks) > 0
    # Each chunk should have a web reference with a URL
    for chunk in gm.grounding_chunks:
        assert chunk.web is not None, "Expected web field in grounding chunk"
        assert chunk.web.uri, f"Expected URI in grounding chunk, got: {chunk.web}"
