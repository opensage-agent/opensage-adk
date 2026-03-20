import importlib
import logging
import os
from typing import Optional

import google.adk as adk
from dotenv import load_dotenv
from google.adk.agents.llm_agent import ToolUnion
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.function_tool import FunctionTool

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.general.bash_tool import bash_tool_main


def mk_agent(opensage_session_id: str):
    return OpenSageAgent(
        name="patch_generation_agent",
        model=LiteLlm(model="anthropic/claude-sonnet-4-20250514"),
        description="Generates Python patch scripts for vulnerabilities.",
        instruction="""
        You are a dummy agent. You should use bash_tool_main to echo "Hello, world!" and get the output.
        """,
        tools=[
            bash_tool_main,
        ],
    )
