import importlib
import logging
import os
from typing import Optional

import google.adk as adk
from dotenv import load_dotenv
from google.adk.agents.llm_agent import ToolUnion
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.agent_tool import AgentTool

from aigise.agents.aigise_agent import AigiseAgent
from aigise.session import get_aigise_session
from aigise.toolbox.build_utils.arvo.compile_and_run import run_poc_from_script
from aigise.toolbox.coverage.tools import (
    find_testcases_covering_function,
    run_coverage,
    show_coverage,
)
from aigise.toolbox.debugger.gdb_mcp.get_toolset import get_toolset as get_gdb_toolset
from aigise.toolbox.eval_submission.cybergym.submission import generate_poc_and_submit
from aigise.toolbox.finish_task.finish_task import finish_task
from aigise.toolbox.fuzzing.fuzz_tools import (
    check_fuzzing_stats,
    extract_crashes,
    run_fuzzing_campaign,
    simplified_python_fuzzer,
)
from aigise.toolbox.general.agent_tools import (
    agent_ensemble,
    complain,
    critique,
    get_available_agents_for_ensemble,
    get_available_models,
    note_suspicious_things,
    think,
)
from aigise.toolbox.general.bash_tool import bash_tool_main
from aigise.toolbox.general.bash_tools_interface import (
    get_background_task_output,
    list_available_scripts,
    list_background_tasks,
    run_terminal_command,
)
from aigise.toolbox.general.dynamic_subagent import (
    call_subagent_as_tool,
    create_subagent,
    list_active_agents,
)
from aigise.toolbox.retrieval.search_tools import (
    get_line_around_linenum_in_file,
    list_functions_in_file,
    search_symbol_definition,
)
from aigise.toolbox.static_analysis.cpg import (
    get_call_paths_to_function,
    get_callee,
    get_caller,
    joern_query,
    joern_slice,
    neo4j_query,
    search_function,
)


def mk_agent(aigise_session_id: str):
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
    gdb_toolset = get_gdb_toolset(aigise_session_id)

    debugger_agent = AigiseAgent(
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
            list_available_scripts,
            list_background_tasks,
            run_terminal_command,
            create_subagent,
            call_subagent_as_tool,
            list_active_agents,
        ],
    )
    root_agent = debugger_agent
    return root_agent
