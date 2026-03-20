"""Normalize tools when adding them to OpenSageAgent.

Public API:
- make_toollike_safe_dict
- make_toollikes_safe_dict
- make_tool_safe_dict
"""

from __future__ import annotations

import asyncio
import inspect
import traceback
from types import FunctionType, MethodType
from typing import Any, Callable, Optional, TypeVar

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext

F = TypeVar("F", bound=Callable[..., Any])


def _dictify_tool_result(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"result": value}


def _is_mcp_tool(tool: BaseTool) -> bool:
    try:
        from google.adk.tools.mcp_tool.mcp_tool import (
            McpTool,  # pylint: disable=g-import-not-at-top
        )

        return isinstance(tool, McpTool)
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _is_mcp_toolset(toolset: BaseToolset) -> bool:
    try:
        from google.adk.tools.mcp_tool.mcp_toolset import (
            McpToolset,  # pylint: disable=g-import-not-at-top
        )

        return isinstance(toolset, McpToolset)
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _recreate_function_with_merged_globals(
    func: Callable[..., Any], extra_globals: dict[str, Any]
) -> Callable[..., Any]:
    new_globals = dict(func.__globals__)
    for key, value in extra_globals.items():
        new_globals.setdefault(key, value)
    return FunctionType(
        func.__code__,
        new_globals,
        func.__name__,
        func.__defaults__,
        func.__closure__,
    )


def _make_safe_dict_callable(func: F) -> F:
    original_func = inspect.unwrap(func)
    original_globals = getattr(original_func, "__globals__", {})

    async def async_wrapper(*args, **kwargs):
        try:
            raw = await func(*args, **kwargs)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return {
                "success": False,
                "error": (
                    f"Failed: {type(exc).__name__}: {exc}\n\nBacktrace:\n"
                    f"{traceback.format_exc()}"
                ),
            }
        return _dictify_tool_result(raw)

    def sync_wrapper(*args, **kwargs):
        try:
            raw = func(*args, **kwargs)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return {
                "success": False,
                "error": (
                    f"Failed: {type(exc).__name__}: {exc}\n\nBacktrace:\n"
                    f"{traceback.format_exc()}"
                ),
            }
        return _dictify_tool_result(raw)

    if asyncio.iscoroutinefunction(func):
        rebuilt = _recreate_function_with_merged_globals(
            async_wrapper, original_globals
        )
    else:
        rebuilt = _recreate_function_with_merged_globals(sync_wrapper, original_globals)

    for attr in (
        "__module__",
        "__name__",
        "__qualname__",
        "__doc__",
        "__annotations__",
        "__sandbox_requirements__",
    ):
        if hasattr(func, attr):
            setattr(rebuilt, attr, getattr(func, attr))

    rebuilt.__wrapped__ = func  # type: ignore[attr-defined]
    try:
        rebuilt.__signature__ = inspect.signature(func)  # type: ignore[attr-defined]
    except (TypeError, ValueError):
        pass
    return rebuilt  # type: ignore[return-value]


def _make_base_tool_safe_dict(tool: BaseTool) -> BaseTool:
    if _is_mcp_tool(tool):
        return tool

    if getattr(tool, "_opensage_safe_dictified", False):
        return tool

    bound = tool.run_async
    orig_func = getattr(bound, "__func__", bound)
    setattr(tool, "_opensage_orig_run_async_func", orig_func)

    async def wrapped_run_async(
        self, *, args: dict[str, Any], tool_context: ToolContext
    ) -> Any:
        try:
            raw = await self._opensage_orig_run_async_func(
                self, args=args, tool_context=tool_context
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return {
                "success": False,
                "error": (
                    f"Failed: {type(exc).__name__}: {exc}\n\nBacktrace:\n"
                    f"{traceback.format_exc()}"
                ),
            }

        if getattr(self, "is_long_running", False) and not raw:
            return raw
        return _dictify_tool_result(raw)

    tool.run_async = MethodType(wrapped_run_async, tool)  # type: ignore[assignment]
    setattr(tool, "_opensage_safe_dictified", True)
    return tool


def _make_toolset_safe_dict(toolset: BaseToolset) -> BaseToolset:
    if _is_mcp_toolset(toolset):
        return toolset
    if getattr(toolset, "_opensage_safe_toolset_dictified", False):
        return toolset

    bound = toolset.get_tools
    orig_func = getattr(bound, "__func__", bound)
    setattr(toolset, "_opensage_orig_get_tools_func", orig_func)

    async def wrapped_get_tools(self, readonly_context=None):
        tools = await self._opensage_orig_get_tools_func(self, readonly_context)
        return [make_toollike_safe_dict(t) for t in (tools or [])]

    toolset.get_tools = MethodType(wrapped_get_tools, toolset)  # type: ignore[assignment]
    setattr(toolset, "_opensage_safe_toolset_dictified", True)
    return toolset


def make_toollike_safe_dict(obj: Any) -> Any:
    if isinstance(obj, BaseToolset):
        return _make_toolset_safe_dict(obj)
    if isinstance(obj, BaseTool):
        return _make_base_tool_safe_dict(obj)
    if callable(obj):
        return _make_safe_dict_callable(obj)
    return obj


def make_toollikes_safe_dict(tools: Optional[list[Any]]) -> list[Any]:
    return [make_toollike_safe_dict(t) for t in (tools or [])]


def make_tool_safe_dict(obj: Any) -> BaseTool:
    if isinstance(obj, BaseTool):
        return _make_base_tool_safe_dict(obj)
    if callable(obj):
        safe_callable = _make_safe_dict_callable(obj)
        return _make_base_tool_safe_dict(FunctionTool(safe_callable))
    raise TypeError(f"Unsupported tool type: {type(obj)}")
