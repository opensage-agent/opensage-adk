from google.adk.tools import ToolContext


def finish_task(tool_context: ToolContext) -> str:
    """Indicate that the task has been finished.

    Args:
    Returns:
        str: "Task finished"
    """
    tool_context.state["task_finished"] = True
    return "Task finished"
