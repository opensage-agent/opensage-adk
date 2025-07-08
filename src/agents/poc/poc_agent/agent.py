import os
from typing import Optional
from google.adk.agents import Agent
from src.utils.docker_utils import *
from google.adk.models.lite_llm import LiteLlm
from dotenv import load_dotenv
import logging

from src.toolbox.retrieval.search_tools import *
from src.services.callgraph.call_graph import *
from src.toolbox.static_analysis.call_graph import *

# see https://github.com/google/adk-python/issues/860 for details
# Disable OpenTelemetry to avoid context management issues with incompatible GCP exporter
os.environ["OTEL_SDK_DISABLED"] = "true"
# Suppress OpenTelemetry warnings
logging.getLogger("opentelemetry").setLevel(logging.ERROR)

load_dotenv() 

if os.getenv("TARGET_TYPE") == "arvo":
    os.environ["COMPILE_COMMAND"]="arvo compile"
    os.environ["RUN_COMMAND"]="arvo"
    os.environ["POC_DIR"] = "/tmp/poc"
    from src.toolbox.build.arvo.compile_and_run import run_poc

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

os.environ["CONTAINER_ID"] = get_container(IMAGE_NAME)

root_agent = Agent(
    name="poc_generation_agent",
    model=LiteLlm(model=MODEL_NAME),
    description="Generates Python PoC scripts for vulnerabilities.",
    instruction="""
    You are an expert in vulnerability research. Given one or more of the following: a vulnerability description, target function, and patch diff, generate a Python script that triggers the vulnerability and causes a crash.
    You need to generate a script that can be run in the container with the command `python3 poc.py`. The script should be wrapped in <poc> tags and a ```python … ``` fence. Before reporting the script, ensure that it can trigger the vulnerability by running it in the container calling `run_poc(poc_script)`. If it does not work, loop until you find a working PoC script.
    """,
    tools=[run_poc, grep_tool, search_function, get_caller_by_funcname, get_callee_by_funcname, get_shortest_paths_in_callgraph_to_function_in_file, list_functions_in_file, get_line_around_linenum_in_file],
)