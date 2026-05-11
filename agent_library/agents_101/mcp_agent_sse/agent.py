from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseConnectionParams

from opensage.agents.opensage_agent import OpenSageAgent

root_agent = OpenSageAgent(
    name="mcp_client_agent",
    model=LiteLlm(model="anthropic/claude-opus-4-7"),
    instruction="You can use filesystem tools.",
    tools=[
        MCPToolset(
            connection_params=SseConnectionParams(url="http://127.0.0.1:3001/sse")
        )
    ],
)
