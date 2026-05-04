# Agent with Web Search

**Source:** [`examples/agents_with_features/sample_web_search`](https://github.com/opensage-agent/opensage-adk/tree/main/examples/agents_with_features/sample_web_search)

Adds `WebSearchTool` so the agent can answer questions that need **fresh information from the web**. The tool uses provider-side search (the LLM provider performs the search and returns grounded answers).

## Agent Source Code

```python title="examples/agents_with_features/sample_web_search/agent.py"
from google.adk.models.lite_llm import LiteLlm

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.general.web_search_tool import WebSearchTool



def mk_agent(opensage_session_id: str):
    return OpenSageAgent(
        name="web_search_agent",
        model=LiteLlm(model="anthropic/claude-opus-4-7"),
        description="An agent with server-side web search.",
        instruction=(
            "You are a helpful assistant with web search capability. "
            "Use web search to answer questions that need up-to-date information. "
            "Keep answers concise."
        ),
        tools=[WebSearchTool(search_context_size="medium")],
    )
```

## Using Google Search Tools

Alternatively, you can also use **Gemini with built-in Google Search grounding** via ADK's `GoogleSearchTool`. Unlike `WebSearchTool`, this tool targets Google Gemini directly rather than routing through LiteLlm.

```python title="examples/agents_with_features/sample_google_search/agent.py"
from google.adk.models.google_llm import Gemini
from google.adk.tools.google_search_tool import GoogleSearchTool


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
```

## Run It

```bash
uv run opensage web \
  --agent examples/agents_with_features/sample_web_search \
  --config examples/agents_with_features/sample_web_search/config.toml \
  --port 8000
```

Requires a provider that supports web-search tool use (for example, Anthropic's `claude-sonnet-4-6`). `search_context_size` controls how much search context is pulled in per query.
