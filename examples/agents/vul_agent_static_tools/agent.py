import asyncio
import logging

from google.adk import Runner
from google.adk.artifacts import InMemoryArtifactService
from google.adk.models.lite_llm import LiteLlm
from google.adk.sessions import InMemorySessionService

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.features import (
    enable_neo4j_logging,
)
from opensage.session import get_opensage_session
from opensage.toolbox.general.bash_tool import bash_tool_main
from opensage.toolbox.retrieval.search_tools import (
    get_line_around_linenum_in_file,
    grep_tool,
    list_functions_in_file,
)
from opensage.toolbox.static_analysis.cpg import (
    get_callee,
    get_caller,
    joern_query,
    joern_slice,
    neo4j_query,
    search_function,
)

logger = logging.getLogger(__name__)


def mk_agent(opensage_session_id="vulnerability-detection-agent-session"):
    enable_neo4j_logging()
    opensage_session = get_opensage_session(opensage_session_id)
    ensemble_manager = opensage_session.ensemble
    ensemble_manager.add_thread_safe_tool("grep_tool")
    ensemble_manager.add_thread_safe_tool("search_function")
    ensemble_manager.add_thread_safe_tool("get_caller_by_funcname")
    ensemble_manager.add_thread_safe_tool("get_callee_by_funcname")
    ensemble_manager.add_thread_safe_tool("list_functions_in_file")
    ensemble_manager.add_thread_safe_tool("get_line_around_linenum_in_file")
    ensemble_manager.add_thread_safe_tool("neo4j_query")
    # ensemble_manager.add_thread_safe_tool("joern_slice")
    # ensemble_manager.add_thread_safe_tool("joern_query")
    config = opensage_session.config
    config.agent_ensemble.available_models_for_ensemble = [
        "anthropic/claude-sonnet-4-5-20250929",
        "openai/o4-mini",
        "openai/gpt-5",
    ]
    opensage_session.config = config
    vul_detect_agent = OpenSageAgent(
        name="vulnerability_detection_agent",
        model=LiteLlm(model="anthropic/claude-sonnet-4-5-20250929"),
        description="find vulnerabilities existing in this function.",
        instruction="""
        You are an expert in vulnerability research. Given a function, detect if any vulnerability exists in this function.
        You need to first understand the function, and extract context of this function (including caller, callee, etc). And then analyze if any vulnerability exists in this function based on the context.
        """,
        tools=[
            # run_poc_from_script,
            grep_tool,
            search_function,
            get_caller,
            get_callee,
            neo4j_query,
            # joern_slice,
            # joern_query,
            # get_shortest_paths_in_callgraph_to_function_in_file,
            list_functions_in_file,
            get_line_around_linenum_in_file,
            # finish_task,
            # generate_poc_and_submit,
            bash_tool_main,
            # create_subagent,
            # list_active_agents,
            # call_subagent_as_tool,
        ],
    )
    return vul_detect_agent


async def main():
    root_agent = mk_agent()
    user_id = "Vul_detection"
    artifact_service = InMemoryArtifactService()
    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="vul_detect_app", user_id=user_id
    )

    runner = Runner(
        app_name=session.app_name,
        agent=root_agent,
        artifact_service=artifact_service,
        session_service=session_service,
    )


if __name__ == "__main__":
    asyncio.run(main())
