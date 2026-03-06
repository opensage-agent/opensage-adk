"""Plugin to warn when an agent edits a file it hasn't read first.

Inspired by oh-my-opencode's write-existing-file-guard. Tracks which files
the agent has read via view_file/read_file, and injects a warning when it
tries to edit a file without reading it first.

Does NOT block execution — the agent may have read the file via
run_terminal_command (cat/head), which we can't track, so blocking would
cause false positives.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)

STATE_KEY = "_read_files"

# Tools that count as "reading" a file
READ_TOOLS = {"view_file", "read_file"}

# Tools that edit existing files (where we want the guard)
EDIT_TOOLS = {"str_replace_editor"}


def _normalize_path(path: str) -> str:
    """Normalize a file path for consistent comparison."""
    return os.path.normpath(path)


def _extract_path(tool_args: dict) -> Optional[str]:
    """Extract file path from tool arguments."""
    for key in ("path", "file_path"):
        if key in tool_args and isinstance(tool_args[key], str):
            return tool_args[key]
    return None


class ReadBeforeEditPlugin(BasePlugin):
    """Warn when agent edits a file it hasn't read in the current session."""

    def __init__(self) -> None:
        super().__init__(name="read_before_edit")

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict,
        tool_context: ToolContext,
    ) -> Optional[Dict[str, Any]]:
        """Warn if editing an unread file."""
        if tool.name not in EDIT_TOOLS:
            return None

        path = _extract_path(tool_args)
        if not path:
            return None

        normalized = _normalize_path(path)
        read_files: List[str] = tool_context.state.get(STATE_KEY, [])

        if normalized not in read_files:
            logger.warning(f"[ReadBeforeEdit] Agent editing unread file: {path}")
            # Inject a warning but don't block — return None so the tool runs,
            # but add a pre-tool prompt via actions_before_tool
            # Since we can't inject into a not-yet-executed result, we return
            # None and handle this in after_tool_callback instead.
            # Store the flag so after_tool_callback can add the warning.
            tool_context.state["_read_before_edit_warn"] = path

        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict,
        tool_context: ToolContext,
        result: dict,
    ) -> Optional[Dict[str, Any]]:
        """Track reads and inject warnings for blind edits."""
        # Track file reads
        if tool.name in READ_TOOLS:
            path = _extract_path(tool_args)
            if path:
                normalized = _normalize_path(path)
                read_files: List[str] = tool_context.state.get(STATE_KEY, [])
                if normalized not in read_files:
                    read_files.append(normalized)
                    tool_context.state[STATE_KEY] = read_files
                    logger.debug(f"[ReadBeforeEdit] Tracked read: {path}")

        # Inject warning for blind edits (flagged by before_tool_callback)
        if tool.name in EDIT_TOOLS:
            warn_path = tool_context.state.pop("_read_before_edit_warn", None)
            if warn_path:
                warning = (
                    "\n\n⚠️ WARNING: You edited this file without reading it first. "
                    "This often leads to incorrect old_str matches. "
                    "If the edit failed or produced unexpected results, "
                    "read the file with view_file to see its actual content "
                    "before retrying."
                )
                result["warning"] = result.get("warning", "") + warning

        return None
