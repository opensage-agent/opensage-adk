"""DevOps-Gym agent.

A general-purpose software engineering agent for DevOps tasks:
  - Build & configuration: fix build failures, resolve dependency issues
  - Monitoring: diagnose runtime anomalies using standard system tools
  - Issue resolving: generate patches to fix GitHub issues
  - Test generation: write tests that validate issue fixes
  - End-to-end: multi-stage DevOps workflows

The agent uses terminal commands and file operations as its primary tools.
No debugger sidecars or graph-database memory are needed for these tasks.
"""

from typing import Optional

from google.adk.models import BaseLlm
from google.adk.models.lite_llm import LiteLlm

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.toolbox.finish_task.finish_task import finish_task
from opensage.toolbox.general.agent_tools import (
    agent_ensemble,
    agent_ensemble_pairwise,
    complain,
    get_available_agents_for_ensemble,
    get_available_models,
    think,
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

_SYSTEM_PROMPT = """
You are an expert DevOps engineer and software developer. Your goal is to
complete the task described in the user's message by working inside a
containerised environment.

## Environment
- Your working directory is `/workspace`.
- The project repository and any relevant files are already set up in the
  container by the task environment.
- Standard Linux tools (bash, git, Maven/Gradle, Go, curl, htop, ps, etc.)
  are available depending on the task.

## General workflow
1. **Read the task carefully.** Understand what is being asked before acting.
2. **Explore first.** Use `run_terminal_command` to understand the current
   state: list files, check git status, read error logs, inspect running
   processes, etc.
3. **Plan.** Use `think` to reason about the best approach before making
   changes.
4. **Act precisely.** Make targeted changes using `str_replace_edit` for
   source files or `run_terminal_command` for build/run commands.
5. **Verify.** Confirm your changes had the intended effect (e.g. re-run the
   failing build, check CPU usage, inspect output files).
6. **Finish.** Call `finish_task` when you are confident the task is done.

## Task-specific guidance

### Build & Configuration tasks
- Read the build failure log (often referenced in the task instruction).
- Identify the root cause: missing dependency, wrong version, config error.
- Fix the relevant build file (pom.xml, build.gradle, go.mod, etc.).
- Confirm with a successful build run.

### Monitoring tasks
- Use `run_terminal_command` with tools like `top`, `htop`, `ps`, `watch`,
  `curl`, `df`, `netstat`, `lsof` to observe system behaviour over time.
- Identify the anomaly pattern (cpu-usage, memory-leak, disk-leak, etc.).
- Write your final answer to `/workspace/monitor_result.txt` with two lines:
  Line 1: the pattern name (one lowercase word, e.g. cpu-usage, memory-leak)
  Line 2: the reason/explanation for the anomaly

### Issue Resolving tasks
- Locate the relevant source files referenced in the issue description.
- Reproduce the bug if possible, then fix it.
- Verify the fix with available tests (`mvn test`, `go test ./...`, etc.).

### Test Generation tasks
- Write tests that reproduce the described issue and verify the fix.
- Place tests in the appropriate test directory following the project's
  existing conventions.

## Important rules
- Do NOT read, search for, or try to execute any evaluation or grading
  scripts that may exist in the environment.
- Always verify your work before calling `finish_task`.
- If you are stuck after several attempts, use `create_subagent` or
  `agent_ensemble_pairwise` to get a fresh perspective.
"""


def mk_agent(
    opensage_session_id: str,
    model: Optional[BaseLlm] = None,
):
    if model is None:
        model = LiteLlm(model="openai/gpt-4o-mini")

    root_agent = OpenSageAgent(
        name="devops_gym_agent",
        model=model,
        description="DevOps-Gym agent for build, monitoring, issue-resolving, and test-generation tasks.",
        instruction=_SYSTEM_PROMPT,
        tools=[
            finish_task,
            # Reasoning
            think,
            complain,
            # File operations
            view_file,
            str_replace_edit,
            # Terminal
            run_terminal_command,
            list_background_tasks,
            get_background_task_output,
            list_available_scripts,
            # Multi-agent
            agent_ensemble,
            agent_ensemble_pairwise,
            get_available_agents_for_ensemble,
            get_available_models,
            create_subagent,
            list_active_agents,
            call_subagent_as_tool,
        ],
        enabled_skills=None,
    )

    return root_agent
