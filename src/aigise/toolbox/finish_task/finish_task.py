from google.adk.tools import ToolContext

from aigise.toolbox.decorators import safe_tool_execution


@safe_tool_execution
def finish_task(tool_context: ToolContext) -> str:
    """Indicate that the task has been finished.

    Args:
        tool_context: The tool context

    Returns:
        None
    """
    tool_context._invocation_context.session.state["task_finished"] = True
