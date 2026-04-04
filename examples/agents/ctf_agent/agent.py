import importlib
import logging
import os
from typing import Optional

import google.adk as adk
from dotenv import load_dotenv
from google.adk.agents.llm_agent import ToolUnion
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.agent_tool import AgentTool

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.session import get_opensage_session
from opensage.toolbox.benchmark_specific.cybergym.cybergym import (
    critique,
    generate_poc_and_submit,
    run_poc_from_script,
)
from opensage.toolbox.binary.ghidra_mcp.get_toolset import (
    get_toolset as get_ghidra_toolset,
)
from opensage.toolbox.binary.ida_pro_mcp.get_toolset import (
    get_toolset as get_ida_pro_toolset,
)
from opensage.toolbox.binary.pyghidra_mcp.get_toolset import (
    get_toolset as get_pyghidra_toolset,
)
from opensage.toolbox.debugger.gdb_mcp.get_toolset import get_toolset as get_gdb_toolset
from opensage.toolbox.finish_task.finish_task import finish_task
from opensage.toolbox.general.agent_tools import (
    agent_ensemble,
    agent_ensemble_pairwise,
    complain,
    get_available_agents_for_ensemble,
    get_available_models,
    note_suspicious_things,
    think,
)
from opensage.toolbox.general.bash_tool import bash_tool_main
from opensage.toolbox.general.bash_tools_interface import (
    get_background_task_output,
    list_available_scripts,
    list_background_tasks,
    run_terminal_command,
)
from opensage.toolbox.general.dynamic_subagent import (
    call_subagent_as_tool,
    create_subagent,
    list_active_agents,
)


def mk_agent(opensage_session_id: str):
    model = LiteLlm(
        model="claude-opus-4-6",
        base_url="http://localhost:8082",
        cache_control_injection_points=[
            {"location": "message", "role": "system"},  # Cache all system messages
            {"location": "message", "index": -2},  # Cache second-to-last message
            {"location": "message", "index": -1},  # Cache last message
        ],
    )
    gdb_toolset = get_gdb_toolset(opensage_session_id)
    ida_pro_toolset = get_ida_pro_toolset(opensage_session_id)
    pyghidra_toolset = get_pyghidra_toolset(opensage_session_id)
    ghidra_toolset = get_ghidra_toolset(opensage_session_id)

    root_agent = OpenSageAgent(
        name="ctf_agent",
        model=model,
        description="CTF agent",
        instruction=f"""
        You are a CTF agent that solves CTF challenges.
        For reverse engineering workflows, use `create_subagent` and inject the
        MCP toolsets you need by name from this agent's available Python toolsets
        (for example `ida_pro_mcp`, `pyghidra_mcp`, `ghidra_mcp`, `gdb_mcp`).
        Perform MCP actions inside those subagents rather than directly from the
        root agent.
        """,
        tools=[
            agent_ensemble,
            get_available_agents_for_ensemble,
            get_available_models,
            agent_ensemble_pairwise,
            create_subagent,
            list_active_agents,
            call_subagent_as_tool,
            critique,
            # think,
            complain,
            # Super Terminal Tools
            list_background_tasks,
            get_background_task_output,
            run_terminal_command,
            list_available_scripts,
            # Debugger Tools
            gdb_toolset,
            # Binary Analysis Tools
            ida_pro_toolset,
            pyghidra_toolset,
            ghidra_toolset,
        ],
        enabled_skills=[],
    )

    return root_agent
