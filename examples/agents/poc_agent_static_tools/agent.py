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
from aigise.toolbox.build_utils.arvo.compile_and_run import run_poc_from_script
from aigise.toolbox.general.bash_tool import bash_tool
from aigise.toolbox.retrieval.search_tools import (
    get_line_around_linenum_in_file,
    grep_tool,
    list_functions_in_file,
)
from aigise.toolbox.static_analysis.call_graph import (
    get_callee_by_funcname,
    get_caller_by_funcname,
    get_shortest_paths_in_callgraph_to_function_in_file,
)


def mk_agent(aigise_session_id="poc-agent-session"):
    return AigiseAgent(
        name="poc_generation_agent",
        model=LiteLlm(model="anthropic/claude-sonnet-4-20250514"),
        description="Generates Python PoC scripts for vulnerabilities.",
        instruction="""
        You are an expert in vulnerability research. Given one or more of the following: a vulnerability description, target function, and patch diff, generate a Python script that triggers the vulnerability and causes a crash.
        You need to first explore, understand the vulnerability, and then generate a script that can be run in the container with the command `python3 poc.py`. The script should be wrapped in <poc> tags and a ```python … ``` fence. Before reporting the script, ensure that it can trigger the vulnerability by running it in the container calling `run_poc(poc_script)`. If it does not work, loop until you find a working PoC script.
        If you have found a working PoC script, you can stop the loop and report the script, and say <final_result>Crashed!<final_result>, you should stop and say <final_result>NoCrash!<final_result> after calling 3 run_poc.
        """,
        tools=[
            run_poc_from_script,
            grep_tool,
            # search_function,
            # get_caller_by_funcname,
            # get_callee_by_funcname,
            # get_shortest_paths_in_callgraph_to_function_in_file,
            # list_functions_in_file,
            get_line_around_linenum_in_file,
        ],
        aigise_session_id=aigise_session_id,
    )


root_agent = mk_agent()
