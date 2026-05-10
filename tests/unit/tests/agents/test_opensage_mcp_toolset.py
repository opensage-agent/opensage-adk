from __future__ import annotations

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.flows.llm_flows.functions import handle_function_call_list_async
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.session import Session
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, SseConnectionParams
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from opensage.agents.opensage_agent import (
    OpenSageAgent,
    OpenSageMCPToolset,
    opensage_default_tool_error_handler,
)


class RaisingTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(name="raising_tool", description="Always raises")

    async def run_async(self, *, args: dict, tool_context: ToolContext):
        raise RuntimeError("injected failure")


def test_opensage_agent_adds_default_tool_error_handler() -> None:
    agent = OpenSageAgent(name="root", model="openai/gpt-4o-mini", tools=[])

    assert agent.canonical_on_tool_error_callbacks == [
        opensage_default_tool_error_handler
    ]


def test_opensage_agent_preserves_custom_tool_error_handler_order() -> None:
    def custom_handler(tool, args, tool_context, error):
        return None

    agent = OpenSageAgent(
        name="root",
        model="openai/gpt-4o-mini",
        tools=[],
        on_tool_error_callback=custom_handler,
    )

    assert agent.canonical_on_tool_error_callbacks == [
        custom_handler,
        opensage_default_tool_error_handler,
    ]


@pytest.mark.asyncio
async def test_default_tool_error_handler_prevents_exception_propagation() -> None:
    tool = RaisingTool()
    agent = OpenSageAgent(
        name="root",
        model="openai/gpt-4o-mini",
        tools=[tool],
    )
    invocation_context = InvocationContext(
        invocation_id="invocation-id",
        agent=agent,
        session=Session(id="session-id", app_name="app", user_id="user"),
        session_service=InMemorySessionService(),
    )

    response_event = await handle_function_call_list_async(
        invocation_context=invocation_context,
        function_calls=[
            types.FunctionCall(
                id="call-id",
                name="raising_tool",
                args={},
            )
        ],
        tools_dict={"raising_tool": tool},
    )

    assert response_event is not None
    function_response = response_event.content.parts[0].function_response
    assert function_response.name == "raising_tool"
    assert function_response.id == "call-id"
    assert function_response.response["success"] is False
    assert "RuntimeError: injected failure" in function_response.response["error"]


@pytest.mark.asyncio
async def test_mcp_toolset_error_message_includes_name_and_url(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def fake_execute_with_session(
        self,
        coroutine_func,
        error_message: str,
        readonly_context=None,
    ):
        captured["error_message"] = error_message
        raise ConnectionError(error_message)

    monkeypatch.setattr(McpToolset, "_execute_with_session", fake_execute_with_session)

    toolset = OpenSageMCPToolset(
        name="test_mcp",
        connection_params=SseConnectionParams(url="http://127.0.0.1:1234/sse"),
    )

    with pytest.raises(ConnectionError):
        await toolset._execute_with_session(
            lambda session: session.list_tools(),
            "Failed to get tools from MCP server",
        )

    assert "Failed to get tools from MCP server" in captured["error_message"]
    assert "name='test_mcp'" in captured["error_message"]
    assert "url='http://127.0.0.1:1234/sse'" in captured["error_message"]
