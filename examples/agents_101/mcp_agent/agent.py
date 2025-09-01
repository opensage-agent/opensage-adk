from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseConnectionParams

root_agent = LlmAgent(
    name="mcp_client_agent",
    model=LiteLlm(model="anthropic/claude-sonnet-4-20250514"),
    instruction="You can use filesystem tools.",
    tools=[
        MCPToolset(
            connection_params=SseConnectionParams(url="http://127.0.0.1:3001/sse")
        )
    ],
)
