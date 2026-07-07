"""Patch ADK to handle tool-not-found gracefully.

ADK's `_get_tool_and_context` raises ValueError when the model calls a
non-existent tool, killing the entire agent session. This patch wraps
`_execute_single_function_call_async` to catch the error and return
an error function_response to the model, letting it self-correct.
"""

from __future__ import annotations

import logging

from google.adk.flows.llm_flows import functions as _adk_functions
from google.genai import types

logger = logging.getLogger(__name__)

_original_execute = None


async def _patched_execute_single_function_call_async(
    invocation_context,
    function_call,
    tools_dict,
    agent,
    tool_confirmation=None,
):
    """Wrap _execute_single_function_call_async to catch tool-not-found."""
    try:
        return await _original_execute(
            invocation_context, function_call, tools_dict, agent, tool_confirmation
        )
    except ValueError as e:
        if "not found" in str(e).lower():
            available = ", ".join(sorted(tools_dict.keys()))
            error_msg = (
                f"Tool '{function_call.name}' not found. "
                f"Available tools: [{available}]. "
                f"Please use one of the available tools."
            )
            logger.warning(error_msg)

            from google.adk.events.event import Event

            return Event(
                id=Event.new_id(),
                invocation_id=invocation_context.invocation_id,
                author=invocation_context.agent.name,
                branch=invocation_context.branch,
                content=types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=function_call.id or "",
                                name=function_call.name,
                                response={"error": error_msg},
                            )
                        )
                    ],
                ),
            )
        raise  # Re-raise non-tool-not-found ValueErrors


def apply() -> None:
    global _original_execute

    _original_execute = _adk_functions._execute_single_function_call_async
    _adk_functions._execute_single_function_call_async = (
        _patched_execute_single_function_call_async
    )

    logger.info("Applied ADK tool-not-found patch")
