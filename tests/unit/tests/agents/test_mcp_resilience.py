"""Tests for MCP resilience: get_tools ConnectionError, tool call timeout, tool exception handling."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.adk.tools.mcp_tool.mcp_toolset import SseConnectionParams
from mcp.types import Tool as McpBaseTool

from opensage.agents.opensage_agent import (
    MCP_TOOL_CALL_TIMEOUT,
    OpenSageMcpTool,
    OpenSageMCPToolset,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mcp_base_tool(name: str = "test_tool") -> McpBaseTool:
    return McpBaseTool(name=name, description="stub", inputSchema={"type": "object"})


def _make_opensage_mcp_tool(name: str = "test_tool") -> OpenSageMcpTool:
    return OpenSageMcpTool(
        mcp_tool=_make_mcp_base_tool(name),
        mcp_session_manager=MagicMock(),
    )


def _make_tool_context() -> MagicMock:
    ctx = MagicMock()
    ctx._invocation_context = MagicMock()
    ctx.tool_confirmation = None
    return ctx


# ---------------------------------------------------------------------------
# Problem 1: get_tools() ConnectionError → agent should survive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tools_returns_empty_on_connection_error(monkeypatch):
    """When MCP server is unreachable, get_tools returns [] instead of crashing."""
    toolset = OpenSageMCPToolset(
        name="gdb_mcp",
        connection_params=SseConnectionParams(url="http://127.0.0.1:9999/sse"),
    )

    async def _boom(self, *a, **kw):
        raise ConnectionError("Failed to get tools from MCP server")

    monkeypatch.setattr(
        "google.adk.tools.mcp_tool.mcp_toolset.McpToolset.get_tools", _boom
    )

    result = await toolset.get_tools()
    assert result == []


@pytest.mark.asyncio
async def test_get_tools_returns_empty_on_timeout_error(monkeypatch):
    """When MCP server times out during tool listing, get_tools returns []."""
    toolset = OpenSageMCPToolset(
        name="gdb_mcp",
        connection_params=SseConnectionParams(url="http://127.0.0.1:9999/sse"),
    )

    async def _timeout(self, *a, **kw):
        raise TimeoutError("Timed out waiting for MCP server")

    monkeypatch.setattr(
        "google.adk.tools.mcp_tool.mcp_toolset.McpToolset.get_tools", _timeout
    )

    result = await toolset.get_tools()
    assert result == []


@pytest.mark.asyncio
async def test_get_tools_returns_empty_on_os_error(monkeypatch):
    """OSError (e.g. connection refused) is also caught."""
    toolset = OpenSageMCPToolset(
        name="gdb_mcp",
        connection_params=SseConnectionParams(url="http://127.0.0.1:9999/sse"),
    )

    async def _os_error(self, *a, **kw):
        raise OSError("Connection refused")

    monkeypatch.setattr(
        "google.adk.tools.mcp_tool.mcp_toolset.McpToolset.get_tools", _os_error
    )

    result = await toolset.get_tools()
    assert result == []


# ---------------------------------------------------------------------------
# Problem 1b: get_tools() replaces ADK MCPTool with OpenSageMcpTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tools_wraps_adk_tools_as_opensage_mcp_tools(monkeypatch):
    """Tools returned by get_tools are OpenSageMcpTool, not bare ADK MCPTool."""
    from google.adk.tools.mcp_tool.mcp_tool import MCPTool as AdkMCPTool

    fake_adk_tool = MagicMock(spec=AdkMCPTool)
    fake_adk_tool.raw_mcp_tool = _make_mcp_base_tool("my_tool")
    fake_adk_tool._mcp_session_manager = MagicMock()
    fake_adk_tool._require_confirmation = False
    fake_adk_tool._header_provider = None
    fake_adk_tool._progress_callback = None

    async def _fake_super_get_tools(self, *a, **kw):
        return [fake_adk_tool]

    monkeypatch.setattr(
        "google.adk.tools.mcp_tool.mcp_toolset.McpToolset.get_tools",
        _fake_super_get_tools,
    )

    toolset = OpenSageMCPToolset(
        name="test_mcp",
        connection_params=SseConnectionParams(url="http://127.0.0.1:9999/sse"),
    )

    tools = await toolset.get_tools()
    assert len(tools) == 1
    assert isinstance(tools[0], OpenSageMcpTool)
    assert tools[0].name == "my_tool"


# ---------------------------------------------------------------------------
# Problem 2: Tool execution has read_timeout_seconds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_async_impl_passes_read_timeout_seconds():
    """_run_async_impl passes read_timeout_seconds to session.call_tool."""
    tool = _make_opensage_mcp_tool("my_tool")

    mock_session = AsyncMock()
    mock_response = MagicMock()
    mock_response.model_dump.return_value = {"result": "ok"}
    mock_session.call_tool = AsyncMock(return_value=mock_response)

    tool._mcp_session_manager.create_session = AsyncMock(return_value=mock_session)

    tool_context = _make_tool_context()
    credential = MagicMock()
    credential.oauth2 = None
    credential.http = None
    credential.api_key = None
    credential.service_account = None

    await tool._run_async_impl(
        args={"x": 1}, tool_context=tool_context, credential=credential
    )

    mock_session.call_tool.assert_called_once()
    call_kwargs = mock_session.call_tool.call_args
    assert call_kwargs[1]["read_timeout_seconds"] == MCP_TOOL_CALL_TIMEOUT


@pytest.mark.asyncio
async def test_mcp_tool_call_timeout_is_5_minutes():
    """MCP_TOOL_CALL_TIMEOUT is 5 minutes."""
    assert MCP_TOOL_CALL_TIMEOUT == timedelta(seconds=300)


# ---------------------------------------------------------------------------
# Helpers for end-to-end tool call tests (error injected at session.call_tool)
# ---------------------------------------------------------------------------


def _make_tool_with_session_error(name: str, error: Exception) -> OpenSageMcpTool:
    """Build an OpenSageMcpTool whose session.call_tool raises `error`.

    The error propagates through the real chain:
    session.call_tool → _run_async_impl → run_async (our try/except).
    """
    tool = _make_opensage_mcp_tool(name)
    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(side_effect=error)
    tool._mcp_session_manager.create_session = AsyncMock(return_value=mock_session)
    return tool


def _make_tool_with_session_success(name: str, payload: dict) -> OpenSageMcpTool:
    """Build an OpenSageMcpTool whose session.call_tool returns `payload`."""
    tool = _make_opensage_mcp_tool(name)
    mock_session = AsyncMock()
    mock_response = MagicMock()
    mock_response.model_dump.return_value = payload
    mock_session.call_tool = AsyncMock(return_value=mock_response)
    tool._mcp_session_manager.create_session = AsyncMock(return_value=mock_session)
    return tool


# ---------------------------------------------------------------------------
# Problem 3: Tool exceptions don't kill the agent
#
# Errors are injected at session.call_tool and must propagate through the
# real _run_async_impl → ADK run_async → our run_async try/except.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_async_catches_mcp_timeout():
    """McpError(REQUEST_TIMEOUT) from read_timeout_seconds → error dict.

    This is the exact error the MCP SDK raises when read_timeout_seconds
    fires inside session.call_tool → send_request → anyio.fail_after.
    """
    from mcp.shared.exceptions import McpError
    from mcp.types import ErrorData

    tool = _make_tool_with_session_error(
        "slow_tool",
        McpError(ErrorData(code=408, message="Timed out waiting for response")),
    )
    result = await tool.run_async(args={}, tool_context=_make_tool_context())

    assert isinstance(result, dict)
    assert "error" in result
    assert "slow_tool" in result["error"]
    assert "McpError" in result["error"]


@pytest.mark.asyncio
async def test_run_async_catches_connection_error():
    """ConnectionError from dead MCP transport → error dict."""
    tool = _make_tool_with_session_error(
        "broken_tool",
        ConnectionError("MCP session task has already terminated"),
    )
    result = await tool.run_async(args={}, tool_context=_make_tool_context())

    assert isinstance(result, dict)
    assert "error" in result
    assert "broken_tool" in result["error"]
    assert "ConnectionError" in result["error"]


@pytest.mark.asyncio
async def test_run_async_catches_timeout_error():
    """Plain TimeoutError (e.g. asyncio.wait_for) → error dict."""
    tool = _make_tool_with_session_error(
        "hung_tool",
        TimeoutError("timed out"),
    )
    result = await tool.run_async(args={}, tool_context=_make_tool_context())

    assert isinstance(result, dict)
    assert "error" in result
    assert "hung_tool" in result["error"]
    assert "TimeoutError" in result["error"]


@pytest.mark.asyncio
async def test_run_async_catches_generic_exception():
    """Any unexpected exception from session.call_tool → error dict."""
    tool = _make_tool_with_session_error(
        "buggy_tool",
        RuntimeError("unexpected internal error"),
    )
    result = await tool.run_async(args={}, tool_context=_make_tool_context())

    assert isinstance(result, dict)
    assert "error" in result
    assert "buggy_tool" in result["error"]
    assert "RuntimeError" in result["error"]


@pytest.mark.asyncio
async def test_run_async_passes_through_on_success():
    """When session.call_tool succeeds, run_async returns the normal result."""
    tool = _make_tool_with_session_success("good_tool", {"content": [{"text": "ok"}]})
    result = await tool.run_async(args={}, tool_context=_make_tool_context())

    assert isinstance(result, dict)
    assert "error" not in result
    assert result == {"content": [{"text": "ok"}]}
