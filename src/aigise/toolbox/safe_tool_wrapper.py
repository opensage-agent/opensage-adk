"""Safe tool wrappers for consistent tool behavior.

Goals:
- Ensure tool results are always dict-like for downstream plugins piggybacking.
- Convert uncaught exceptions into {"success": False, "error": "..."}.
- Preserve tool name/description/schema so LLM calling remains stable.

This module intentionally avoids patching ADK internals.
"""

from __future__ import annotations

import asyncio
import traceback
from typing import Any, Awaitable, Callable, Optional, TypeVar

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext

F = TypeVar("F", bound=Callable[..., Any])


def _dictify_tool_result(value: Any) -> dict[str, Any]:
    """Coerce arbitrary tool result to a dict payload."""
    if isinstance(value, dict):
        return value
    return {"result": value}


class SafeToolWrapper(BaseTool):
    """A BaseTool wrapper that enforces dict results and safe errors."""

    def __init__(self, inner: BaseTool):
        super().__init__(
            name=inner.name,
            description=getattr(inner, "description", "") or "",
            is_long_running=getattr(inner, "is_long_running", False),
            custom_metadata=getattr(inner, "custom_metadata", None),
        )
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        # Delegate unknown attributes to the inner tool (e.g., require_confirmation
        # flags, tool-specific helpers).
        return getattr(self._inner, name)

    def _get_declaration(self):
        return self._inner._get_declaration()  # pylint: disable=protected-access

    async def run_async(
        self, *, args: dict[str, Any], tool_context: ToolContext
    ) -> Any:
        try:
            raw = await self._inner.run_async(args=args, tool_context=tool_context)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return {
                "success": False,
                "error": (
                    f"Failed: {type(exc).__name__}: {exc}\n\nBacktrace:\n"
                    f"{traceback.format_exc()}"
                ),
            }
        # Preserve ADK long-running semantics: a long-running tool may return a falsy
        # value (e.g. None/"") to indicate "no function_response event".
        if getattr(self._inner, "is_long_running", False) and not raw:
            return raw
        return _dictify_tool_result(raw)


def _is_safe_wrapped_toolset(toolset: BaseToolset) -> bool:
    return bool(getattr(toolset, "_aigise_safe_toolset_wrapped", False))


def ensure_safe_toolset(toolset: BaseToolset) -> BaseToolset:
    """Ensure toolset.get_tools returns SafeToolWrapper-wrapped tools.

    We patch the instance method in-place to avoid changing isinstance(toolset, McpToolset)
    checks elsewhere (important for prompt policies and toolset introspection).
    """
    if _is_safe_wrapped_toolset(toolset):
        return toolset

    original_get_tools = toolset.get_tools

    async def wrapped_get_tools(
        readonly_context=None,
    ):  # pylint: disable=missing-function-docstring
        tools = await original_get_tools(readonly_context)
        wrapped: list[BaseTool] = []
        for t in tools or []:
            wrapped.append(ensure_safe_tool(t))
        return wrapped

    # Preserve any custom attributes (e.g. name/tool_name_prefix) on the instance.
    toolset.get_tools = wrapped_get_tools  # type: ignore[assignment]
    setattr(toolset, "_aigise_safe_toolset_wrapped", True)
    return toolset


def ensure_safe_tool(obj: Any) -> BaseTool:
    """Coerce a tool-like object to a safe BaseTool."""
    if isinstance(obj, SafeToolWrapper):
        return obj
    if isinstance(obj, BaseTool):
        return SafeToolWrapper(obj)
    if callable(obj):
        return SafeToolWrapper(FunctionTool(obj))
    raise TypeError(f"Unsupported tool type: {type(obj)}")


def ensure_safe_toollike(obj: Any) -> Any:
    """Wrap tools/toolsets to ensure safe, dict-shaped tool results.

    - BaseTool -> SafeToolWrapper(BaseTool)
    - callable -> FunctionTool(callable) -> SafeToolWrapper
    - BaseToolset -> patch get_tools to wrap returned tools; return toolset unchanged
    """
    if isinstance(obj, BaseToolset):
        return ensure_safe_toolset(obj)
    if isinstance(obj, (BaseTool, SafeToolWrapper)) or callable(obj):
        return ensure_safe_tool(obj)
    return obj


def ensure_safe_toollikes(tools: Optional[list[Any]]) -> list[Any]:
    """Apply ensure_safe_toollike to a list, preserving order."""
    out: list[Any] = []
    for t in tools or []:
        out.append(ensure_safe_toollike(t))
    return out
