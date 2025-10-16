from google.adk.tools import ToolContext


def finish_task(tool_context: ToolContext) -> str:
    """Indicate that the task has been finished.

    Args:
        tool_context: The tool context

    Returns:
        None
    """
    tool_context.session.state["task_finished"] = True
