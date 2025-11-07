from google.adk.tools import ToolContext

from aigise.toolbox.decorators import safe_tool_execution


@safe_tool_execution
def finish_task(tool_context: ToolContext) -> str:
    """Indicate that the task has been finished.

    Args:
        tool_context: The tool context

    Returns:
        "Task finished"
    """
    tool_context.state["task_finished"] = True
    return "Task finished"
