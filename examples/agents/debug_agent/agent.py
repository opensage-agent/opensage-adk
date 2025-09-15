import importlib
import logging
import os

from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseConnectionParams

from aigise.extended_features.sec_agent import SecAgent
from aigise.toolbox.mcp_tools.debugger.gdb_mcp.get_toolset import (
    get_toolset as get_gdb_toolset,
)

# Disable OpenTelemetry to avoid context management issues with incompatible GCP exporter
# see https://github.com/google/adk-python/issues/860 for details
os.environ["OTEL_SDK_DISABLED"] = "true"
# Suppress OpenTelemetry warnings
logging.getLogger("opentelemetry").setLevel(logging.ERROR)
MODEL_NAME = os.getenv("MODEL_NAME", "anthropic/claude-sonnet-4-20250514")

gdb_toolset = get_gdb_toolset()

root_agent = SecAgent(
    name="debug_agent",
    model=LiteLlm(model=MODEL_NAME),
    description="Generates Python PoC scripts for vulnerabilities.",
    instruction="""
    You are an debugger AI agent.
    """,
    tools=[gdb_toolset],
)
