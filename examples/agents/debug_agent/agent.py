import uuid

from google.adk.models.lite_llm import LiteLlm

from aigise.agents.aigise_agent import AigiseAgent
from aigise.toolbox.mcp_tools.debugger.gdb_mcp.get_toolset import (
    get_toolset as get_gdb_toolset,
)


def mk_agent(aigise_session_id="debug-agent-session"):
    gdb_toolset = get_gdb_toolset(aigise_session_id)

    return AigiseAgent(
        name="debug_agent",
        aigise_session_id=aigise_session_id,
        model=LiteLlm(model="anthropic/claude-sonnet-4-20250514"),
        description="Generates Python PoC scripts for vulnerabilities.",
        instruction="""
      You are an debugger AI agent.
      """,
        tools=[gdb_toolset],
    )


root_agent = mk_agent()
