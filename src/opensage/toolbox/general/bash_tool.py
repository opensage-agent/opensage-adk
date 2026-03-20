from google.adk.tools import ToolContext

from opensage.toolbox.sandbox_requirements import requires_sandbox
from opensage.utils.agent_utils import get_sandbox_from_context


@requires_sandbox("main")
def bash_tool_main(command: str, tool_context: ToolContext) -> str:
    """Execute a bash command and return the output.
    Call this tool only if other tools cannot handle your current needs.

    Args:
        command (str): The bash command to execute
    Returns:
        str: The output of the bash command
    """
    sandbox = get_sandbox_from_context(tool_context, "main")
    return sandbox.run_command_in_container(command, timeout=60)
