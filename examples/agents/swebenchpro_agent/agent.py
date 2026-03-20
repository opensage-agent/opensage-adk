import os
from typing import Optional

from google.adk.models import BaseLlm
from google.adk.models.lite_llm import LiteLlm
from google.adk.planners import BasePlanner

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.memory.search_tool import search_memory
from opensage.session import get_opensage_session
from opensage.toolbox.finish_task.finish_task import finish_task
from opensage.toolbox.general.agent_tools import (
    agent_ensemble,
    agent_ensemble_pairwise,
    complain,
    get_available_agents_for_ensemble,
    get_available_models,
)
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
from opensage.toolbox.general.fileop import str_replace_edit, view_file


def mk_agent(
    opensage_session_id: str,
    model: Optional[BaseLlm] = None,
    planner: Optional[BasePlanner] = None,
):
    # Use provided model or fallback to default
    if model is None:
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

    opensage_session = get_opensage_session(opensage_session_id)
    run_poc_command = opensage_session.config.build.run_command

    root_agent = OpenSageAgent(
        name="benchmark_agent",
        model=model,
        planner=planner,
        instruction=f"""
        You are an expert software engineer specializing in fixing bugs in large codebases.
        Your task is to fix the issue described in the problem statement.

        You have access to a variety of tools, including terminal commands, file manipulation, memory system, and specialized analysis skills.
        Your are allowed to use tests under the project to help you verify the correctness.

        **Guidelines:**

        1.  **Check Exploration Summary FIRST**: If an exploration summary exists in your context, READ IT CAREFULLY before doing anything else.
            - Go directly to "Critical Files (MUST READ)" and view those files first
            - Check "NOT FOUND" section - do NOT search for items listed there
            - Use the "Bug Location Hypothesis" as your starting point
            - Note "Project-Specific Notes" for indentation style and test commands
        2.  **Search Memory**: Use `search_memory` to check if similar bugs or patterns have been encountered before.
        3.  **Analyze**: Carefully read the problem statement. Understand the expected behavior vs. the actual behavior.
        4.  **View**: Use `view_file` to read file contents with line numbers before editing. Pay attention to the indentation hint in the output.
        5.  **Reproduce**: Create a reproduction script (e.g., `reproduce_issue.py`) to confirm the bug. This is CRITICAL.
        6.  **Reason**: Before making changes, explain your plan.
        7.  **Edit**: Modify the code to fix the issue using `str_replace_edit`. The old_string must be UNIQUE in the file.
            - IMPORTANT: Match the file's indentation style exactly (TABS vs SPACES).
        8.  **IMPORTANT: If you are completly stuck, it probably means that you are exploring a wrong vulnerable function, you should try create a subagent with no history to solve the task, as your history might be misleading.**
        9. **Verify**: Run your reproduction script again to confirm the fix. Run related project tests. If tests fail, READ the test code to understand what the test expects
        10. **Submit**: When confident AND tests pass, call `finish_task`.


        **Important Notes:**
        *   The repository is cloned in the current working directory (or `repo` subdirectory). For SWE-bench Pro tasks, it is located at `/app`.
        *   Always check for background tasks using `list_background_tasks` if you initiate long-running processes.
        *   If you get stuck, try searching for error messages or keywords in the codebase. Or create a subagent with no history to solve the task, as your history might be misleading. Do not give up easily.
        *  If you stuck on a task, or if you are think a subtask is complex, you should using agent_ensemble_pairwise tools to do the subtask with multiple models, this will help you to think out of the box and try different approaches.
        *   Long tool outputs are automatically saved to files. Check these directories if you need full content:
            - `/workspace/.tool_outputs/` - Full outputs from truncated/summarized tool responses
            - `/workspace/.memory_observer_outputs/` - Full outputs saved by memory system

        **Search Termination Rules:**
        *   If you search for a specific class/function/variable 3+ times with different methods (grep, find, git log) and still cannot find it, STOP and consider:
            - The problem statement may reference code that doesn't exist in this version
            - The naming may be different than expected (check for similar patterns)
            - document it and move on
        *   Do NOT keep searching indefinitely for something that may not exist.

        **Common Project-Specific Issues:**
        *   Python projects may require virtual environment activation.
        *   Some tests require services like Redis/MongoDB - check if they're running before testing.

        **Multi-File Change Checklist (CRITICAL):**
        Before calling finish_task, verify your changes are COMPLETE:
        *   Did you modify ALL files that reference the changed function/class?
        *   If you added a new function, is it properly imported where needed?
        *   If you changed a function signature, did you update ALL callers?
        *   If you modified a utility function, check if wrapper/helper functions also need updates
        *   Run `git diff` to review ALL your changes before submitting

        **Output Format:**
        You should generate a patch file named `prediction.patch` in /shared directory.
        The patch file should be a valid patch file that can be applied to the repository using `git apply`.
        You can generate the patch file by calling `git diff` in the repository.
        """,
        tools=[
            finish_task,
            # File Operations (Claude Code style)
            view_file,
            str_replace_edit,
            # subagent
            agent_ensemble,
            get_available_agents_for_ensemble,
            get_available_models,
            agent_ensemble_pairwise,
            create_subagent,
            list_active_agents,
            call_subagent_as_tool,
            # Terminal Tools
            list_background_tasks,
            get_background_task_output,
            run_terminal_command,
            list_available_scripts,
            # Memory Tools
            search_memory,
        ],
        enabled_skills=None,
    )

    return root_agent


def mk_explore_agent(
    opensage_session_id: str,
    model: Optional[BaseLlm] = None,
    planner: Optional[BasePlanner] = None,
):
    """Create an exploration agent that thoroughly explores the codebase.

    This agent should run BEFORE the main benchmark agent to gather context
    about the codebase related to the given task. It does NOT solve the problem,
    but focuses on exploration and returning detailed findings.

    Note: Knowledge is automatically stored by MemoryObserverPlugin.
    """
    # Use provided model or fallback to default
    if model is None:
        model = LiteLlm(
            model="litellm_proxy/openai/gpt-5",
            api_key=os.environ.get("LITELLM_PROXY_API_KEY"),
            base_url="https://litellm-991596698159.us-west1.run.app/",
            cache_control_injection_points=[
                {"location": "message", "role": "system"},
                {"location": "message", "index": -2},
                {"location": "message", "index": -1},
            ],
        )

    explore_agent = OpenSageAgent(
        name="explore_agent",
        model=model,
        planner=planner,
        instruction="""
        You are an expert code explorer. Your PRIMARY task is to thoroughly explore the codebase and return detailed findings related to the given problem statement.

        **IMPORTANT**: You are NOT solving the problem. Your goal is to:
        1. Understand the codebase structure
        2. Identify relevant files, functions, and classes
        3. Discover patterns, conventions, and important code structures
        4. Return comprehensive findings to the next agent

        **Guidelines:**

        1.  **Analyze the Problem**: Read the problem statement to understand what areas of the codebase are relevant.
        2.  **Explore Structure**: Use terminal commands (`ls`, `find`, `tree`) to understand the project structure.
        3.  **Search for Relevant Code**: Use `grep` to find files and functions related to the problem.
        4.  **Read Files**: Use `view_file` to read files with line numbers.
        5.  **Provide Detailed Summary**: Before calling `finish_task`, you MUST output a comprehensive summary of ALL your findings.

        **Final Summary Format (REQUIRED):**
        Before finishing, provide a detailed summary in this EXACT format. The main agent DEPENDS on this structure:
        ```
        ## Exploration Summary

        ### 1. Critical Files (MUST READ)
        List the top 3-5 files the main agent MUST read, with specific line numbers:
        - `/path/to/file1.py` (lines 50-120): [What this section contains and why it matters]
        - `/path/to/file2.py` (lines 200-250): [What this section contains and why it matters]

        ### 2. Bug Location Hypothesis
        Based on exploration, the bug is MOST LIKELY in:
        - File: [exact path]
        - Function/Class: [name]
        - Lines: [approximate range]
        - Reason: [why you think the bug is here]

        ### 3. NOT FOUND (Important!)
        List any classes/functions/variables mentioned in the problem statement that you COULD NOT find:
        - [item1]: Searched using [methods], not found in codebase
        - [item2]: May not exist or may be named differently
        If the problem references non-existent code, STATE THIS CLEARLY.

        ### 4. Project-Specific Notes
        - Indentation style: [TABS / 2-SPACES / 4-SPACES]
        - Test command: [how to run tests, e.g., `npm test`, `pytest`]
        - Required services: [Redis/MongoDB/etc. if needed]
        - Special setup: [any environment setup needed]

        ### 5. Recommended Fix Strategy
        1. [First step]
        2. [Second step]
        3. [Verification step]

        ### 6. Related Files (IMPORTANT)
        List ALL files that may need changes together:
        - Main file: [path] - [what needs to change]
        - Related file 1: [path] - [why it might need updates]
        - Related file 2: [path] - [why it might need updates]
        If a function is used in multiple places, LIST ALL LOCATIONS.

        ### 7. Test Files
        List test files that MUST be run to verify the fix:
        - [test file path]: [relevant test functions]
        - Test command: [exact command to run tests]

        ### 8. Code Structure Context
        - [Describe relevant architecture]
        - [Class hierarchies, function call flows if relevant]
        ```

        **CRITICAL**: If you cannot find something mentioned in the problem statement after thorough searching,
        you MUST document this in section "3. NOT FOUND". This saves the main agent from wasting time searching
        for code that doesn't exist.

        **Important Notes:**
        *   The repository is located at `/app`.
        *   Do NOT modify any code.
        *   Focus on THOROUGH EXPLORATION - the more details you find, the better.
        *   Your summary is CRITICAL - it will be the main input for the next agent.
        *   Knowledge is automatically recorded - focus on exploration quality.
        *   Long tool outputs are automatically saved to files. Check these directories if you need full content:
            - `/workspace/.tool_outputs/` - Full outputs from truncated/summarized tool responses
            - `/workspace/.memory_observer_outputs/` - Full outputs saved by memory system
        """,
        tools=[
            finish_task,
            # File Operations
            view_file,
            # Terminal Tools for exploration
            list_background_tasks,
            get_background_task_output,
            run_terminal_command,
            list_available_scripts,
        ],
        enabled_skills=None,
    )

    return explore_agent
