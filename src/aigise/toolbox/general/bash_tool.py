from google.adk.tools import ToolContext

from aigise.toolbox.decorators import requires_sandbox
from aigise.utils.agent_utils import get_sandbox_from_context


@requires_sandbox("main")
def bash_tool(command: str, context: ToolContext) -> str:
    """Execute a bash command and return the output.

    Args:
        command: The bash command to execute

    Returns:
        The output of the bash command
    """
    sandbox = get_sandbox_from_context(context, "main")
    return sandbox.run_command_in_container(command, timeout=300)
