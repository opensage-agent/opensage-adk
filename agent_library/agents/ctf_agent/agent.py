import os

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.benchmark_specific.cybergym.cybergym import critique
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
from opensage.toolbox.general.agent_tools import complain
from opensage.toolbox.general.bash_tools_interface import (
    get_background_task_output,
    list_background_tasks,
    run_terminal_command,
)
from opensage.toolbox.general.orchestration_tools import (
    call_subagent,
    continue_agent_instance,
    create_subagent,
    get_available_models,
    list_subagents,
    send_message,
    wait_for_subagent,
)
from opensage.toolbox.general.sandbox_management import (
    create_sandbox,
    exec_in_sandbox,
    list_active_sandboxes,
    stop_sandbox,
)
from opensage.toolbox.general.view_image import view_image

from .models import DEFAULT_MODEL


def mk_agent(opensage_session_id: str):
    model = DEFAULT_MODEL
    gdb_toolset = get_gdb_toolset(opensage_session_id)
    ida_pro_toolset = get_ida_pro_toolset(opensage_session_id)
    pyghidra_toolset = get_pyghidra_toolset(opensage_session_id)
    ghidra_toolset = get_ghidra_toolset(opensage_session_id)

    root_agent = OpenSageAgent(
        name="ctf_agent",
        model=model,
        description="CTF agent",
        instruction="""
        You are a CTF agent that solves CTF challenges.
        For reverse engineering workflows, use `create_subagent` and inject the
        MCP toolsets you need by name from this agent's available Python toolsets
        (for example `ida_pro_mcp`, `pyghidra_mcp`, `ghidra_mcp`, `gdb_mcp`).
        Perform MCP actions inside those subagents rather than directly from the
        root agent.
        """,
        tools=[
            get_available_models,
            create_subagent,
            call_subagent,
            continue_agent_instance,
            send_message,
            wait_for_subagent,
            view_image,
            list_subagents,
            critique,
            # think,
            complain,
            # Super Terminal Tools
            list_background_tasks,
            get_background_task_output,
            run_terminal_command,
            # Debugger Tools
            gdb_toolset,
            # Binary Analysis Tools
            ida_pro_toolset,
            pyghidra_toolset,
            ghidra_toolset,
            # Sandbox Management
            create_sandbox,
            exec_in_sandbox,
            list_active_sandboxes,
            stop_sandbox,
            # Task lifecycle
            finish_task,
        ],
        enabled_skills=["mmp", "workflow"],
    )

    return root_agent
