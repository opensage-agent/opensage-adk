from google.adk.tools import ToolContext

from aigise.toolbox.decorators import requires_sandbox
from aigise.utils.agent_utils import get_sandbox_from_context


@requires_sandbox("main")
def submit_submission(tool_context: ToolContext) -> str:
    """Submit a submission to the Cybergym platform and get feedback.

    Args:
        command: The command to submit the submission

    Returns:
        The output of the submission
    """
    sandbox = get_sandbox_from_context(tool_context, "main")
    return sandbox.run_command_in_container(
        f"cd /shared/ && ./submit.sh /tmp/poc", timeout=300
    )
