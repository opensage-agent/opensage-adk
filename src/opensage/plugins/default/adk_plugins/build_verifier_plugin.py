"""Plugin to verify code builds before allowing finish_task to complete.

For known project types (Go, Python), automatically runs build verification.
For unknown project types, prompts agent on first attempt; allows on second.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from opensage.utils.agent_utils import get_sandbox_from_context

logger = logging.getLogger(__name__)

STATE_KEY_FINISH_TASK_ATTEMPTS = "_build_verifier_finish_attempts"


class BuildVerifierPlugin(BasePlugin):
    """Plugin to verify build passes before allowing task completion."""

    def __init__(self) -> None:
        super().__init__(name="build_verifier")

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict,
        tool_context: ToolContext,
        result: dict,
    ) -> Optional[Dict[str, Any]]:
        """Intercept finish_task and verify build passes first."""
        if tool.name != "finish_task":
            return None

        attempts = tool_context.state.get(STATE_KEY_FINISH_TASK_ATTEMPTS, 0) + 1
        tool_context.state[STATE_KEY_FINISH_TASK_ATTEMPTS] = attempts
        logger.info(f"[BuildVerifier] finish_task attempt #{attempts}")

        try:
            sandbox = get_sandbox_from_context(tool_context, "main")
        except Exception as e:
            logger.warning(f"[BuildVerifier] Could not get sandbox: {e}")
            return None

        project_type = self._detect_project_type(sandbox)
        logger.info(f"[BuildVerifier] Detected project type: {project_type}")

        if attempts >= 2:
            logger.info("[BuildVerifier] Agent confirmed (attempt #2+), allowing")
            return None

        build_cmd = self._get_build_command(project_type)

        if not build_cmd:
            logger.info("[BuildVerifier] Unknown project type, prompting agent")
            result["output"] = (
                "BUILD VERIFICATION PROMPT\n"
                "Could not detect project type for automatic build verification.\n\n"
                "Before finishing, please ensure:\n"
                "  1. Your code compiles/builds without errors\n"
                "  2. You have run any relevant tests\n"
                "  3. You have verified your changes work as expected\n\n"
                "If you have already verified, call finish_task again to confirm."
            )
            tool_context.state["task_finished"] = False
            return None

        logger.info(f"[BuildVerifier] Running: {build_cmd}")
        try:
            output, exit_code = sandbox.run_command_in_container(build_cmd, timeout=120)
        except Exception as e:
            logger.warning(f"[BuildVerifier] Build command failed to execute: {e}")
            return None

        if exit_code == 0:
            logger.info("[BuildVerifier] Build passed")
            return None

        logger.warning(f"[BuildVerifier] Build FAILED (exit code {exit_code})")

        max_output_len = 3000
        if len(output) > max_output_len:
            output = output[:max_output_len] + "\n... (truncated)"

        result["output"] = (
            f"BUILD VERIFICATION FAILED\n"
            f"{'=' * 50}\n"
            f"Project type: {project_type}\n"
            f"Command: {build_cmd}\n"
            f"Exit code: {exit_code}\n"
            f"{'=' * 50}\n"
            f"{output}\n"
            f"{'=' * 50}\n\n"
            f"Fix the errors above, or call finish_task again to submit anyway."
        )
        tool_context.state["task_finished"] = False

        return None

    def _detect_project_type(self, sandbox) -> str:
        """Detect the project type based on config files in /app."""
        checks = [
            ("go.mod", "go"),
            ("go.sum", "go"),
            ("pyproject.toml", "python"),
            ("setup.py", "python"),
            ("requirements.txt", "python"),
        ]

        for filename, project_type in checks:
            try:
                _, exit_code = sandbox.run_command_in_container(
                    f"test -f /app/{filename}", timeout=5
                )
                if exit_code == 0:
                    logger.debug(f"[BuildVerifier] Found {filename} -> {project_type}")
                    return project_type
            except Exception:
                continue

        return "unknown"

    def _get_build_command(self, project_type: str) -> Optional[str]:
        """Get build command for known project types only."""
        commands = {
            "go": "cd /app && go build ./... 2>&1",
            "python": (
                "cd /app && "
                "find . -name '*.py' -not -path './.*' -not -path '*/node_modules/*' "
                "-not -path '*/.venv/*' -not -path '*/venv/*' -type f 2>/dev/null | "
                "head -50 | xargs -r python3 -m py_compile 2>&1"
            ),
        }
        return commands.get(project_type)
