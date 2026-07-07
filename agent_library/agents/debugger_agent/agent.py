import importlib
import logging
import os
from typing import Optional

import google.adk as adk
from dotenv import load_dotenv
from google.adk.agents.llm_agent import ToolUnion
from google.adk.models.lite_llm import LiteLlm

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.session import get_opensage_session
from opensage.toolbox.debugger.gdb_mcp.get_toolset import get_toolset as get_gdb_toolset
from opensage.toolbox.general.agent_tools import (
    complain,
    critique,
    note_suspicious_things,
    think,
)
from opensage.toolbox.general.bash_tool import bash_tool_main
from opensage.toolbox.general.bash_tools_interface import (
    get_background_task_output,
    list_background_tasks,
    run_terminal_command,
)
from opensage.toolbox.general.orchestration_tools import (
    call_subagent,
    create_subagent,
    get_available_models,
    list_subagents,
)


def mk_agent(opensage_session_id: str):
    model = LiteLlm(
        # model="litellm_proxy/vertex_ai/claude-sonnet-4-5@20250929",
        model="litellm_proxy/sage-gpt-5",
        # model="litellm_proxy/vertex_ai/claude-sonnet-4",
        api_key=os.environ.get("LITELLM_PROXY_API_KEY"),
        base_url="https://litellm-991596698159.us-west1.run.app/",
        # Auto-inject cache_control for system messages and last 2 messages
        cache_control_injection_points=[
            {"location": "message", "role": "system"},  # Cache all system messages
            {"location": "message", "index": -2},  # Cache second-to-last message
            {"location": "message", "index": -1},  # Cache last message
        ],
    )
    gdb_toolset = get_gdb_toolset(opensage_session_id)

    debugger_agent = OpenSageAgent(
        name="debugger_agent",
        model=model,
        description="A debugger agent that can debug the vulnerable program. When calling this tool, you should tell the debugger what is the vulnerable program and what is the poc, and what is the expected behavior, you should have concrete expectations to check.",
        instruction="""
        You are a debugger agent that can debug the vulnerable program.
        You should use the debugger tool to debug the vulnerable program.
        Only the poc file in /shared can be used as an input to the vulnerable program, if it's not in /shared, you should copy it to /shared.
        You should solve the request using as least number of tools as possible, do not use the step by step tools unless it's absolutely necessary. This is very important.
        If you consistently encounter errors or your remaining LLM call budget is low (< 3), you should stop exploring further and immediately report your progress.
        """,
        tools=[
            complain,
            gdb_toolset,
            list_background_tasks,
            run_terminal_command,
            create_subagent,
            call_subagent,
            list_subagents,
            critique,
        ],
    )
    root_agent = debugger_agent
    return root_agent
