import os

from google.adk.models.lite_llm import LiteLlm

from aigise.agents.aigise_agent import AigiseAgent
from aigise.session import get_aigise_session
from aigise.toolbox.finish_task.finish_task import finish_task
from aigise.toolbox.general.agent_tools import complain
from aigise.toolbox.general.bash_tools_interface import (
    get_background_task_output,
    list_background_tasks,
    run_terminal_command,
)
from aigise.toolbox.general.fileop import (
    edit_file,
    list_dir,
    replace_in_file,
    search_file,
    view_file,
)


def mk_agent(aigise_session_id: str):
    model = LiteLlm(
        model="litellm_proxy/openai/gpt-5",
        api_key=os.environ.get("LITELLM_PROXY_API_KEY"),
        base_url="https://litellm-991596698159.us-west1.run.app/",
        # Auto-inject cache_control for system messages and last 2 messages
        cache_control_injection_points=[
            {"location": "message", "role": "system"},  # Cache all system messages
            {"location": "message", "index": -2},  # Cache second-to-last message
            {"location": "message", "index": -1},  # Cache last message
        ],
    )

    # Before you want to call any tool, you should first reason and explicitly state what the plan is, and call the most appropriate tool to execute the plan, do not execute the bash_tool unless it is absolutely necessary, it's the lowest priority tool.
    # For local testing, you can use run_poc_from_script to generate a poc file and run it locally to test if it triggers the vulnerability. When you feed a poc_generation_script to run_poc_from_script, it will copy the poc file to /tmp/poc in the container containing the vulnerable program, and then execute `arvo`, which will automatically feed /tmp/poc as an input to the vulnerable program.
    # You can submit a poc by calling generate_poc_and_submit. You can also submit a poc file by calling /shared/submit.sh /path/to/poc, if you get a crash from other tools like fuzzing tool or debugger tool, you should manually submit the poc file by calling /shared/submit.sh /path/to/poc.
    # After each submission, if it didn't trigger the vulnerability, you should try fuzzing. Then, if still no crash is triggered, you can try debugging the poc with the debugger tool, see whether it executes the part of code that you assume it would execute, verify your assumptions. You should tell debugger what is the vulnerable program and what is the poc, and what is the expected behavior, you should have concrete expectations to check.

    aigise_session = get_aigise_session(aigise_session_id)
    run_poc_command = aigise_session.config.build.run_command

    root_agent = AigiseAgent(
        name="benchmark_agent",
        model=model,
        instruction=f"""
        You are an expert software engineer specializing in fixing bugs in large codebases.
        Your task is to fix the issue described in the problem statement.

        You have access to a variety of tools, including terminal commands, file manipulation, and specialized analysis skills.
        Your are allowed to use tests under the project to help you verify the correctness.

        **Guidelines:**

        1.  **Analyze**: Carefully read the problem statement and any provided hints. Understand the expected behavior vs. the actual behavior.
        2.  **Explore**: Use terminal commands (e.g., `grep`, `find`, `ls`) or File tools (`list_dir`, `search_file`) to locate relevant files.
        3.  **Reproduce**: Create a reproduction script (e.g., `reproduce_issue.py`) to confirm the bug. This is CRITICAL.
        4.  **Plan**: Before making changes, explain your plan via the `think` tool (if available) or by reasoning out loud.
        5.  **Edit**: Modify the code to fix the issue using `edit_file` or `replace_in_file`.
        6.  **Verify**: Run your reproduction script again to confirm the fix. Ensure you haven't introduced regressions.
        7.  **Submit**: When confident, call `finish_task`.

        **Important Notes:**
        *   The repository is cloned in the current working directory (or `repo` subdirectory). For SWE-bench Pro tasks, it is located at `/app`.
        *   Always check for background tasks using `list_background_tasks` if you initiate long-running processes.
        *   If you get stuck, try searching for error messages or keywords in the codebase.
        *   Do not give up easily. If a fix fails, analyze the error and try a different approach.
        *
        """,
        tools=[
            finish_task,
            # think,
            complain,
            # Super Terminal Tools
            list_background_tasks,
            get_background_task_output,
            run_terminal_command,
            # File Operations
            view_file,
            edit_file,
            search_file,
            replace_in_file,
            list_dir,
        ],
        enabled_skills=[],
    )

    return root_agent
