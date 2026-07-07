from langchain_core.tools import StructuredTool

from patchagent.agent.base import AgentStopException, PatchFoundException
from patchagent.agent.clike.proxy import internal
from patchagent.logger import logger
from patchagent.task import PatchTask


def create_viewcode_tool(task: PatchTask, auto_hint: bool = False) -> StructuredTool:
    def viewcode(path: str, start_line: int, end_line: int) -> str:
        """
        Returns the code snippet, the line number is attached to the head of each line.

        :param path: The path of the file.
        :param start_line: The start line of the code snippet.
        :param end_line: The end line of the code snippet.
        """

        logger.info(f"[📞] viewcode(path={path}, start_line={start_line}, end_line={end_line})")
        args, result = internal.viewcode(task, path, start_line, end_line, auto_hint=auto_hint)
        task.current_context.add_tool_call("viewcode", args, result)
        return result

    return StructuredTool.from_function(viewcode)


def create_locate_tool(task: PatchTask, auto_hint: bool = False) -> StructuredTool:
    def locate(symbol: str) -> str:
        """
        Returns the location of the symbol.

        :param symbol: The symbol to be located.
        """

        logger.info(f"[📞] locate(symbol={symbol})")
        args, result = internal.locate(task, symbol, auto_hint=auto_hint)
        task.current_context.add_tool_call("locate", args, result)
        return result

    return StructuredTool.from_function(locate)


def create_validate_tool(task: PatchTask, auto_hint: bool = False) -> StructuredTool:
    def validate(patch: str) -> str:
        """
        Returns the validation result of the patch. The patch should be a multi-hunk patch, here is a example:
        ```diff
        --- a/src/OT/Layout/GDEF/GDEF.hh
        +++ b/src/OT/Layout/GDEF/GDEF.hh
        @@ -869,7 +869,7 @@ struct GDEF
                return v;

            v = table->get_glyph_props (glyph);
        -      if (likely (table)) // Don't try setting if we are the null instance!
        +      if (likely (table.get_blob ())) // Don't try setting if we are the null instance!
            glyph_props_cache.set (glyph, v);

            return v;
        ```

        :param patch: The patch to be validated.
        """

        logger.info(f"[📞] validate(patch={patch})")
        try:
            args, result = internal.validate(task, patch, auto_hint=auto_hint)
        except PatchFoundException as e:
            task.current_context.add_tool_call("validate", {"patch": str(e)}, "patch found")
            raise
        except AgentStopException:
            task.current_context.add_tool_call("validate", {"patch": patch}, "agent stop")
            raise

        task.current_context.add_tool_call("validate", args, result)
        return result

    return StructuredTool.from_function(validate)
