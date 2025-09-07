# deploy_mcp_server.py - Separate Cloud Run service using Streamable HTTP
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import anyio
import click
import mcp.types as types
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.mcp_tool.conversion_utils import adk_to_mcp_tool_type
from loguru import logger
from mcp import types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import Receive, Scope, Send


def calculate_multiplication(a: int, b: int) -> int:
    return a * b


adk_tool_to_expose = FunctionTool(calculate_multiplication)


def create_mcp_server():
    """Create and configure the MCP server."""
    app = Server("adk-mcp-streamable-server")

    @app.call_tool()
    async def call_mcp_tool(
        name: str, arguments: dict
    ) -> list[mcp_types.Content]:  # MCP uses mcp_types.Content
        """MCP handler to execute a tool call requested by an MCP client."""
        print(
            f"MCP Server: Received call_tool request for '{name}' with args: {arguments}"
        )

        # Check if the requested tool name matches our wrapped ADK tool
        if name == adk_tool_to_expose.name:
            try:
                # Execute the ADK tool's run_async method.
                # Note: tool_context is None here because this MCP server is
                # running the ADK tool outside of a full ADK Runner invocation.
                # If the ADK tool requires ToolContext features (like state or auth),
                # this direct invocation might need more sophisticated handling.
                adk_tool_response = await adk_tool_to_expose.run_async(
                    args=arguments,
                    tool_context=None,
                )
                print(
                    f"MCP Server: ADK tool '{name}' executed. Response: {adk_tool_response}"
                )

                # Format the ADK tool's response (often a dict) into an MCP-compliant format.
                # Here, we serialize the response dictionary as a JSON string within TextContent.
                # Adjust formatting based on the ADK tool's output and client needs.
                response_text = json.dumps(adk_tool_response, indent=2)
                # MCP expects a list of mcp_types.Content parts
                return [mcp_types.TextContent(type="text", text=response_text)]

            except Exception as e:
                print(f"MCP Server: Error executing ADK tool '{name}': {e}")
                # Return an error message in MCP format
                error_text = json.dumps(
                    {"error": f"Failed to execute tool '{name}': {str(e)}"}
                )
                return [mcp_types.TextContent(type="text", text=error_text)]
        else:
            # Handle calls to unknown tools
            print(f"MCP Server: Tool '{name}' not found/exposed by this server.")
            error_text = json.dumps(
                {"error": f"Tool '{name}' not implemented by this server."}
            )
            return [mcp_types.TextContent(type="text", text=error_text)]

    @app.list_tools()
    async def list_mcp_tools() -> list[mcp_types.Tool]:
        """MCP handler to list tools this server exposes."""
        print("MCP Server: Received list_tools request.")
        # Convert the ADK tool's definition to the MCP Tool schema format
        mcp_tool_schema = adk_to_mcp_tool_type(adk_tool_to_expose)
        print(f"MCP Server: Advertising tool: {mcp_tool_schema.name}")
        return [mcp_tool_schema]

    return app


def main(port: int = 9998, json_response: bool = False):
    """Main server function."""
    logging.basicConfig(level=logging.INFO)

    app = create_mcp_server()

    # Create session manager with stateless mode for scalability
    session_manager = StreamableHTTPSessionManager(
        app=app,
        event_store=None,
        json_response=json_response,
        stateless=True,  # Important for Cloud Run scalability
    )

    async def handle_streamable_http(
        scope: Scope, receive: Receive, send: Send
    ) -> None:
        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        """Manage session manager lifecycle."""
        async with session_manager.run():
            logger.info("MCP Streamable HTTP server started!")
            try:
                yield
            finally:
                logger.info("MCP server shutting down...")

    # Create ASGI application
    starlette_app = Starlette(
        debug=False,  # Set to False for production
        routes=[
            Mount("/mcp", app=handle_streamable_http),
        ],
        lifespan=lifespan,
    )

    import uvicorn

    uvicorn.run(starlette_app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
