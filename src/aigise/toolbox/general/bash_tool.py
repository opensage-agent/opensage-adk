from google.adk.tools import ToolContext

from aigise.toolbox.decorators import requires_sandbox, safe_tool_execution
from aigise.utils.agent_utils import get_sandbox_from_context


@safe_tool_execution
@requires_sandbox("main")
def bash_tool(command: str, tool_context: ToolContext) -> str:
    """Execute a bash command and return the output.
    Call this tool only if other tools cannot handle your current needs.

    Args:
        command: The bash command to execute

    Returns:
        The output of the bash command
    """
    sandbox = get_sandbox_from_context(tool_context, "main")
    return sandbox.run_command_in_container(command, timeout=60)
