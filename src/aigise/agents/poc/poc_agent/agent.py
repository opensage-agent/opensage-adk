import importlib
import logging
import os
from typing import Optional

from dotenv import load_dotenv
from google.adk.agents.llm_agent import ToolUnion
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.function_tool import FunctionTool

from aigise.extended_features.function_composer import combined_for, combined_one
from aigise.extended_features.reward_logger import RewardLogger
from aigise.extended_features.sec_agent import SecAgent
from aigise.extended_features.tool_combo_manager import ToolCombo
from aigise.services.callgraph.call_graph import *
from aigise.toolbox.retrieval.search_tools import *
from aigise.toolbox.static_analysis.call_graph import *

target_type = os.getenv("TARGET_TYPE", "default")
if target_type != "default":
    module_path = f"secagentx.toolbox.build.{target_type}.compile_and_run"
    mod = importlib.import_module(module_path)
    run_poc_from_script = getattr(mod, "run_poc_from_script")

# Disable OpenTelemetry to avoid context management issues with incompatible GCP exporter
# see https://github.com/google/adk-python/issues/860 for details
os.environ["OTEL_SDK_DISABLED"] = "true"
# Suppress OpenTelemetry warnings
logging.getLogger("opentelemetry").setLevel(logging.ERROR)

load_dotenv()

CODEQL_DIR = os.getenv("CODEQL_DIR")
if not CODEQL_DIR:
    raise ValueError("CODEQL_DIR environment variable is not set.")

IMAGE_NAME = os.getenv("IMAGE_NAME")
if not IMAGE_NAME:
    raise ValueError("IMAGE_NAME environment variable is not set.")

MODEL_NAME = os.getenv("MODEL_NAME", "anthropic/claude-sonnet-4-20250514")

restart_neo4j()
get_and_upload_call_graph(
    codeql_dir=CODEQL_DIR,
    image_name=IMAGE_NAME,
    build_command=os.getenv("COMPILE_COMMAND"),
)

search_caller_combo = ToolCombo(
    name="search_caller_combo",
    tool_sequences=[search_function, get_caller_by_funcname],
    description="Search function and then get caller",
    model=LiteLlm(model=MODEL_NAME),
    return_history=False,
)

search_callee_combo = ToolCombo(
    name="search_callee_combo",
    tool_sequences=[search_function, get_callee_by_funcname],
    description="Search function and then get callee, should be invoked if 'combo search and callee' is required by the user",
    model=LiteLlm(model=MODEL_NAME),
    return_history=True,
)

# Create combined tool using function_composer
get_callee_search_combined = combined_for(
    get_callee_by_funcname, search_function, "get_callee_and_search"
)
get_caller_search_combined = combined_one(
    get_caller_by_funcname, search_function, "get_caller_and_search"
)


def sanitizer_reward_function(tool_response: dict, message: Optional[str]) -> float:
    """
    Reward function for run_poc tool.
    Returns 1.0 if the output contains "sanitizer", 0.0 otherwise.
    """
    result_str = str(tool_response).lower()
    if "sanitizer" in result_str:
        return 1.0
    return 0.0


def final_result_reward_function(agent_result: dict, message: Optional[str]) -> float:
    """
    Reward function for poc_generation_agent.
    Returns 1.0 if response contains "<final_result>Crashed!</final_result>",
    0.0 if response contains "<final_result>NoCrash!</final_result>",
    -1.0 otherwise.
    """
    # Extract the actual response from agent_result
    response_text = agent_result.get("response", "")
    if not response_text:
        return -1.0

    response_str = str(response_text)
    if "<final_result>Crashed!</final_result>" in response_str:
        return 1.0
    elif "<final_result>NoCrash!</final_result>" in response_str:
        return 0.0
    else:
        return -1.0


poc_reward_logger = RewardLogger(
    reward_function=sanitizer_reward_function, tool_name="run_poc"
)

final_result_reward_logger = RewardLogger(
    reward_function=final_result_reward_function, agent_name="poc_generation_agent"
)

root_agent = SecAgent(
    name="poc_generation_agent",
    model=LiteLlm(model=MODEL_NAME),
    description="Generates Python PoC scripts for vulnerabilities.",
    instruction="""
    You are an expert in vulnerability research. Given one or more of the following: a vulnerability description, target function, and patch diff, generate a Python script that triggers the vulnerability and causes a crash.
    You need to generate a script that can be run in the container with the command `python3 poc.py`. The script should be wrapped in <poc> tags and a ```python … ``` fence. Before reporting the script, ensure that it can trigger the vulnerability by running it in the container calling `run_poc(poc_script)`. If it does not work, loop until you find a working PoC script.
    If you have found a working PoC script, you can stop the loop and report the script, and say <final_result>Crashed!<final_result>, you should stop and say <final_result>NoCrash!<final_result> after calling 3 run_poc.
    """,
    tools=[
        run_poc_from_script,
        grep_tool,
        search_function,
        get_caller_by_funcname,
        get_callee_by_funcname,
        get_shortest_paths_in_callgraph_to_function_in_file,
        list_functions_in_file,
        get_line_around_linenum_in_file,
        get_callee_search_combined,
        get_caller_search_combined,
    ],
    tool_combos=[search_caller_combo, search_callee_combo],
    reward_loggers=[poc_reward_logger, final_result_reward_logger],
)
