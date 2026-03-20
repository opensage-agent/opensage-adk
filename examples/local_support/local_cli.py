import argparse
import asyncio
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import magic
from dotenv import load_dotenv
from google.adk import Runner
from google.adk.agents import RunConfig
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.apps import App
from google.adk.models.lite_llm import LiteLlm
from google.adk.sessions import Session
from google.adk.tools import ToolContext
from google.genai import types
from pydantic_core import to_json

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.features.agent_history_tracker import enable_neo4j_logging
from opensage.features.opensage_in_memory_session_service import (
    OpenSageInMemorySessionService,
)
from opensage.plugins import load_plugins
from opensage.plugins.builtins.adk_plugins.parts_from_tool import (
    PARTS_FROM_TOOLS_ID,
    ImageInjectionPlugin,
)
from opensage.session.opensage_session import get_opensage_session
from opensage.toolbox.general.agent_tools import (
    agent_ensemble,
    agent_ensemble_pairwise,
    complain,
    critique,
    flag_unjustified_claims,
    get_available_models,
    plan,
    think,
)
from opensage.toolbox.general.bash_tools_interface import (
    get_background_task_output,
    list_background_tasks,
    run_terminal_command,
    wait_for_background,
)
from opensage.toolbox.general.dynamic_subagent import (
    call_subagent_as_tool,
    create_subagent,
    list_active_agents,
)

logger = logging.getLogger(__name__)

# enable_neo4j_logging()

session_id = str(uuid4())
app_name = "opensage_local"
user_id = "user_" + session_id
run_until_explicit_finish = True


def update_plan(plan: list[dict[str, str]], explanation: str = "") -> str:
    """Updates the task plan.
    Provide an optional explanation and a list of plan items, each with a step and status.
    At most one step can be in_progress at a time.

    Args:
        plan: Current plan as a list of steps, each step is a dict with 'step' and 'status' keys.
        'step' is the description of the step,
            'status' should be one of: pending, in_progress, completed
        explanation: Explanation for the update

    Returns:
        Success message
    """
    return "Plan updated."


# def exec_cmd(cmd: str | list[str], timeout: float = 600.0, *, tool_context: ToolContext) -> str:
#     """Execute a shell command and return its output.

#     Args:
#         cmd: Command to execute (string or list of strings)
#         timeout: Maximum time to wait for command execution (in seconds), default is 600 seconds
#     Returns:
#         Output of the command
#     """
#     work_dir = tool_context.state.get("work_dir")
#     if isinstance(cmd, list):
#         cmd = shlex.join(cmd)

#     # remove current .venv/bin in PATH env
#     env = os.environ.copy()
#     curr_venv = sys.prefix
#     path_parts = env.get("PATH", "").split(os.pathsep)
#     path_parts = [p for p in path_parts if p != os.path.join(curr_venv, "bin")]
#     env["PATH"] = os.pathsep.join(path_parts)

#     result = subprocess.run(
#         cmd,
#         stdout=subprocess.PIPE,
#         stderr=subprocess.STDOUT,
#         shell=True,
#         cwd=work_dir,
#         env=env,
#     )
#     return {
#         "return_code": result.returncode,
#         "output": result.stdout.decode("utf-8", errors="backslashreplace"),
#     }


def view_image(file_path: str, *, tool_context: ToolContext) -> str:
    """View an local image file.
    Please use this tool if you want to check visual content.

    Args:
        file_path: Path to the image file

    Returns:
        str: Success message
    """
    mime_type = magic.from_file(file_path, mime=True)
    file_path = Path(file_path)
    parts = [types.Part.from_bytes(data=file_path.read_bytes(), mime_type=mime_type)]

    if PARTS_FROM_TOOLS_ID in tool_context.state:
        tool_context.state[PARTS_FROM_TOOLS_ID] += parts
    else:
        tool_context.state[PARTS_FROM_TOOLS_ID] = parts

    return "Image loaded successfully."


def finish_task(*, tool_context: ToolContext) -> str:
    """Indicate that the task has been finished.

    Args:
        tool_context: The tool context

    Returns:
        "Task finished"
    """
    tool_context.state["task_finished"] = True
    return "Task finished"


async def run_agent(
    *,
    config_path: str,
    model_name: str,
    model_reasoning_effort: str | None,
    description: str,
    instruction: str,
    prompt: str,
    max_llm_calls: int,
    work_dir: Path,
    trace_save_path: Path,
    use_subagent: bool,
):
    opensage_session = get_opensage_session(session_id, config_path=config_path)
    await opensage_session.sandboxes.launch_all_sandboxes()

    model = LiteLlm(
        model=model_name,
        api_key=os.environ.get("LITELLM_PROXY_API_KEY"),
        base_url="https://litellm-991596698159.us-west1.run.app/",
        reasoning_effort=model_reasoning_effort,
    )

    tools = [
        finish_task,
        update_plan,
        view_image,
        run_terminal_command,
        list_background_tasks,
        get_background_task_output,
        wait_for_background,
        # critique,
        think,
        # plan,
        # complain,
    ]

    if use_subagent:
        list_active_agents.__name__ = "get_available_agents"
        tools += [
            call_subagent_as_tool,
            get_available_models,
            create_subagent,
            list_active_agents,
            # agent_ensemble,
            agent_ensemble_pairwise,
        ]

    local_agent = OpenSageAgent(
        name="terminal_agent",
        model=model,
        description=description,
        instruction=instruction,
        # tools=[exec_cmd, finish_task, think, critique, plan, complain, update_plan],
        tools=tools,
    )

    enabled_plugins = []
    if opensage_session and getattr(opensage_session, "config", None):
        enabled_plugins = (
            getattr(getattr(opensage_session.config, "plugins", None), "enabled", [])
            or []
        )

    plugins = load_plugins(enabled_plugins)

    session_service = OpenSageInMemorySessionService()
    enabled_plugins = []
    if opensage_session and getattr(opensage_session, "config", None):
        enabled_plugins = (
            getattr(getattr(opensage_session.config, "plugins", None), "enabled", [])
            or []
        )
    plugins = load_plugins(enabled_plugins)
    if plugins:
        logger.warning(
            "Loaded plugins for session %s: %s",
            session_id,
            ", ".join(plugin.name for plugin in plugins),
        )
    app = App(
        name=app_name,
        root_agent=local_agent,
        plugins=[ImageInjectionPlugin()] + plugins,
    )  # temperarily add ImageInjectionPlugin
    runner = Runner(
        app=app,
        session_service=session_service,
    )

    # 3. Create session with opensage_session_id in state
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state={"opensage_session_id": session_id, "work_dir": str(work_dir.absolute())},
    )

    # Helper to track remaining LLM-call budget across multiple runner invocations.
    remaining_llm_calls = max_llm_calls

    def _build_run_config() -> RunConfig:
        """Construct RunConfig reflecting the remaining LLM quota."""
        if remaining_llm_calls is None:
            return RunConfig(max_llm_calls=max_llm_calls)
        return RunConfig(max_llm_calls=remaining_llm_calls)

    async def _update_remaining_and_get_session() -> Session | None:
        """Refresh the cached session and update remaining call budget."""
        nonlocal remaining_llm_calls
        session_snapshot = await session_service.get_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
        if (
            max_llm_calls > 0
            and session_snapshot
            and session_snapshot.state
            and "_adk" in session_snapshot.state
        ):
            used_calls = int(
                session_snapshot.state.get("_adk", {}).get("llm_calls_used", 0) or 0
            )
            remaining_llm_calls = max(0, remaining_llm_calls - used_calls)
        logger.warning(f"Remaining LLM calls: {remaining_llm_calls}")
        logger.warning(f"Used LLM calls: {used_calls}")
        logger.warning(f"Max LLM calls: {max_llm_calls}")
        return session_snapshot

    async def _save_trace():
        logger.info(f"Saving trace to {trace_save_path}")
        session_snapshot = (
            await session_service.get_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )
        ).model_copy(deep=True)
        session_snapshot.events = all_events
        with open(trace_save_path, "wb") as f:
            f.write(to_json(session_snapshot, by_alias=False, indent=2))

    all_events = []
    session_snapshot: Session | None = None

    try:
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            run_config=_build_run_config(),
            new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
        ):
            logger.warning(event.model_dump_json())
            all_events.append(event)
            await _save_trace()

        session_snapshot = await _update_remaining_and_get_session()

        if run_until_explicit_finish:
            task_finished = (
                session_snapshot.state.get("task_finished", False)
                if session_snapshot
                else False
            )
            while not task_finished:
                if max_llm_calls > 0 and remaining_llm_calls <= 0:
                    logger.warning(
                        "LLM-call budget exhausted before task signaled completion; stopping follow-up loop."
                    )
                    break

                async for event in runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    run_config=_build_run_config(),
                    new_message=types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                text=(
                                    "I approve you to continue, if you think the task is complete, you should call the finish_task tool, "
                                    "and then summarize the task and the result without calling any other tool. If last response is marked "
                                    'as "No response.", please don\'t reason too much for the current status, and continue the previous plan. '
                                    "DO NOT respond to this message."
                                )
                            )
                        ],
                    ),
                ):
                    logger.warning(event.model_dump_json(exclude_none=True))
                    all_events.append(event)
                    await _save_trace()

                session_snapshot = await _update_remaining_and_get_session()

                task_finished = (
                    session_snapshot.state.get("task_finished", False)
                    if session_snapshot
                    else False
                )

    except LlmCallsLimitExceededError as e:
        logger.warning(f"Llm calls limit exceeded for session {session_id}: {e}")

    await runner.close()
    await _save_trace()


if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s")
    logger.setLevel(logging.DEBUG)

    parser = argparse.ArgumentParser(
        description="Run the local terminal agent with configurable options."
    )
    parser.add_argument(
        "--config-path",
        default=Path(__file__).parent / "config.toml",
        help="Path to the configuration TOML file used by the agent.",
    )
    parser.add_argument(
        "--model",
        default="openai/gpt-5.2",
        help="Model identifier passed to LiteLlm.",
    )
    parser.add_argument(
        "--model-reasoning-effort",
        default=None,
        help="Model reasoning effort level (e.g., 'low', 'medium', 'high').",
    )
    parser.add_argument(
        "--description",
        default="You are a terminal agent that can execute shell commands to complete tasks.",
        help="Description provided to the agent.",
    )
    parser.add_argument(
        "--instruction",
        default="Please use shell commands to complete the given task.",
        help="Instruction prompt for the agent.",
    )
    parser.add_argument(
        "--prompt",
        default="Please create a python script that prints 'Hello, World!' to the console.",
        help="Initial user prompt sent to the agent.",
    )
    parser.add_argument(
        "--max-llm-calls",
        type=int,
        default=100,
        help="Maximum number of LLM calls allowed per run.",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path.cwd(),
        help="Directory where the agent can create and modify files.",
    )
    parser.add_argument(
        "--trace-save-path",
        type=Path,
        help="Where to store the serialized session trace.",
        required=True,
    )
    parser.add_argument(
        "--dotenv-path",
        type=Path,
        default=Path(__file__).parent / ".env",
    )
    parser.add_argument(
        "--container-id-save-path",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--remove-container-on-exit",
        action="store_true",
        help="If set, remove the Neo4j container on exit.",
    )
    parser.add_argument(
        "--use-subagent",
        action="store_true",
        help="If set, enable subagent features to run the task.",
    )
    parser.add_argument(
        "--sage-api-base",
        type=str,
        help="Base URL for the Sage API server.",
    )

    args = parser.parse_args()
    load_dotenv(dotenv_path=args.dotenv_path)

    container_id_save_path = args.container_id_save_path
    if container_id_save_path:
        container_id = os.environ["NEO4J_CONTAINER_ID"]
        logger.info(f"Write container_id ({container_id}) to {container_id_save_path}")
        with open(container_id_save_path, "w") as f:
            f.write(container_id)

    try:
        asyncio.run(
            run_agent(
                config_path=args.config_path,
                model_name=args.model,
                model_reasoning_effort=args.model_reasoning_effort,
                description=args.description,
                instruction=args.instruction,
                prompt=args.prompt,
                max_llm_calls=args.max_llm_calls,
                work_dir=args.work_dir,
                trace_save_path=args.trace_save_path,
                use_subagent=args.use_subagent,
            )
        )
    finally:
        if args.remove_container_on_exit and container_id_save_path:
            container_id = os.environ["NEO4J_CONTAINER_ID"]
            with httpx.Client(base_url=args.sage_api_base, timeout=300) as client:
                resp = client.post(f"/remove_neo4j/{container_id}")
                logger.info(f"Removed container {container_id}, response: {resp.text}")
