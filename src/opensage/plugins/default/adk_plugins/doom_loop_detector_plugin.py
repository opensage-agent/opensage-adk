"""Plugin to detect doom loops — agent repeating the same failing tool call.

Inspired by opencode's doom loop detection (processor.ts:154-176).
When the agent makes the same tool call N times consecutively (same tool name
and identical arguments), the Nth call is short-circuited with an error message
telling the agent to try a different approach.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)

STATE_KEY = "_doom_loop_history"


def _hash_args(args: dict) -> str:
    """Produce a stable hash of tool arguments for comparison."""
    serialized = json.dumps(args, sort_keys=True, default=str)
    return hashlib.md5(serialized.encode()).hexdigest()


class DoomLoopDetectorPlugin(BasePlugin):
    """Detect and break doom loops — consecutive identical tool calls."""

    def __init__(self, threshold: int = 3) -> None:
        super().__init__(name="doom_loop_detector")
        self.threshold = threshold

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict,
        tool_context: ToolContext,
    ) -> Optional[Dict[str, Any]]:
        """Check if the current call would be a repeated identical call."""
        current_hash = _hash_args(tool_args)
        current_entry = {"tool": tool.name, "args_hash": current_hash}

        history: List[dict] = tool_context.state.get(STATE_KEY, [])

        # Check if all recent entries match the current call
        consecutive = 0
        for entry in reversed(history):
            if (
                entry["tool"] == current_entry["tool"]
                and entry["args_hash"] == current_hash
            ):
                consecutive += 1
            else:
                break

        if consecutive >= self.threshold - 1:
            logger.warning(
                f"[DoomLoopDetector] Detected doom loop: tool '{tool.name}' "
                f"called {consecutive + 1} times with identical arguments"
            )
            # Don't add this call to history — it's being blocked
            return {
                "output": (
                    f"DOOM LOOP DETECTED: You have attempted the exact same "
                    f"'{tool.name}' call {consecutive + 1} times consecutively "
                    f"with identical arguments. This approach is not working.\n\n"
                    f"STOP and try a DIFFERENT approach:\n"
                    f"- Read the file to verify its current content\n"
                    f"- Use a different tool or strategy\n"
                    f"- Adjust your arguments\n"
                    f"- Re-think the problem"
                )
            }

        # Record this call in history, capped at threshold length
        history.append(current_entry)
        if len(history) > self.threshold:
            history = history[-self.threshold :]
        tool_context.state[STATE_KEY] = history

        return None
