import importlib
import logging
import os
from typing import Optional

import google.adk as adk
from dotenv import load_dotenv
from google.adk.agents.llm_agent import ToolUnion
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.function_tool import FunctionTool

from aigise.agents.aigise_agent import AigiseAgent
from aigise.toolbox.general.bash_tool import bash_tool
from aigise.toolbox.retrieval.search_tools import (
    get_line_around_linenum_in_file,
    grep_tool,
)


def mk_agent(aigise_session_id: str):
    return AigiseAgent(
        name="patch_generation_agent",
        model=LiteLlm(model="anthropic/claude-sonnet-4-20250514"),
        description="Generates Python patch scripts for vulnerabilities.",
        instruction="""
        You are a dummy agent. You should use bash_tool to echo "Hello, world!" and get the output.
        """,
        tools=[
            grep_tool,
            bash_tool,
            get_line_around_linenum_in_file,
        ],
    )
