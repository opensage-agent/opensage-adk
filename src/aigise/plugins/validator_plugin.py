"""Plugin to detect and warn about common test execution pitfalls.

This plugin is designed with high precision in mind:
- Prefers false negatives over false positives
- Only warns when patterns are unambiguous
- Uses strict pattern matching
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)


class ValidatorPlugin(BasePlugin):
    """Plugin to validate test commands and provide actionable warnings."""

    def __init__(self) -> None:
        super().__init__(name="validator")

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict,
        tool_context: ToolContext,
        result: dict,
    ) -> Optional[Dict[str, Any]]:
        """Analyze test command results and add warnings if issues detected."""

        # Only process run_terminal_command
        if tool.name != "run_terminal_command":
            logger.debug(
                f"[Validator] Skipping tool '{tool.name}' (not run_terminal_command)"
            )
            return None  # Don't block other plugins

        command = tool_args.get("command", "")
        logger.info(f"[Validator] Processing command: {command[:100]}...")
        output = str(result.get("output", ""))
        exit_code = result.get("exit_code", -1)

        # Collect warnings - only add when confident
        warnings: List[str] = []

        # Detect Go test commands
        go_test_match = self._is_go_test_command(command)
        if go_test_match:
            warnings.extend(self._analyze_go_test(command, output, exit_code))

        # Detect Python pytest commands
        pytest_match = self._is_pytest_command(command)
        if pytest_match:
            warnings.extend(self._analyze_pytest(command, output, exit_code))

        # Detect git diff commands
        if self._is_git_diff_command(command):
            warnings.extend(self._analyze_git_diff(command, output, exit_code))

        # Detect patch/git apply commands
        if self._is_patch_command(command):
            warnings.extend(self._analyze_patch(command, output, exit_code))

        # Add warnings to result if any (modify in-place)
        if warnings:
            warning_text = "\n".join(f"⚠️ {w}" for w in warnings)
            # Append warnings directly to output so LLM sees them prominently
            result["output"] = result.get("output", "") + "\n\n" + warning_text
            logger.warning(
                f"[Validator] Added {len(warnings)} warning(s) to output: {warning_text[:200]}"
            )
        else:
            logger.debug("[Validator] No warnings detected for this command")

        return None  # Don't block other plugins; result is modified in-place

    def _is_go_test_command(self, command: str) -> bool:
        """Check if command is a Go test command.

        Strict matching: must have 'go test' as a command, not just substring.
        """
        # Match: go test, go test -v, go test ./...
        # But not: echo "go test", cargo test
        patterns = [
            r"^go\s+test\b",  # starts with go test
            r"&&\s*go\s+test\b",  # after &&
            r";\s*go\s+test\b",  # after ;
            r"\|\|\s*go\s+test\b",  # after ||
        ]
        return any(re.search(p, command) for p in patterns)

    def _is_pytest_command(self, command: str) -> bool:
        """Check if command is a pytest command.

        Strict matching for pytest invocations.
        """
        patterns = [
            r"^pytest\b",  # starts with pytest
            r"^python[23]?\s+-m\s+pytest\b",  # python -m pytest
            r"&&\s*pytest\b",  # after &&
            r";\s*pytest\b",  # after ;
        ]
        return any(re.search(p, command) for p in patterns)

    def _analyze_go_test(self, command: str, output: str, exit_code: int) -> List[str]:
        """Analyze Go test output for issues. Returns list of warnings."""
        warnings = []

        # 1. Detect cached test results
        # Go format: "ok  \tpkg/path\t(cached)\n" or at end of line
        # The tab before (cached) is distinctive
        if re.search(r"\t\(cached\)(\n|$)", output):
            warnings.append(
                "Test result shows '(cached)' - Go used cached results. "
                "Your recent code changes may NOT have been tested. "
                "Re-run with '-count=1' to force a fresh test run."
            )

        # 2. Detect build failure
        # Go format: "FAIL\tpkg/path [build failed]"
        if re.search(r"\[build failed\]", output):
            warnings.append(
                "Build failed - there are compile errors. "
                "After fixing, re-run tests with '-count=1' to ensure "
                "the fix is properly tested (not cached)."
            )

        # 3. Detect piped to grep with exit code 1
        # This is tricky: exit code 1 from grep means "no match"
        # but user might think it means "test failed"
        if self._is_piped_to_grep(command) and exit_code == 1:
            # Only warn if output is empty or very short (grep found nothing)
            if len(output.strip()) < 10:
                warnings.append(
                    "Command piped through grep returned exit code 1 with little output. "
                    "This likely means grep found no matches, NOT that tests failed. "
                    "Run 'go test' directly to see actual test results and exit code."
                )

        return warnings

    def _analyze_pytest(self, command: str, output: str, exit_code: int) -> List[str]:
        """Analyze pytest output for issues. Returns list of warnings."""
        warnings = []

        # Detect piped to grep with exit code 1
        if self._is_piped_to_grep(command) and exit_code == 1:
            if len(output.strip()) < 10:
                warnings.append(
                    "Command piped through grep returned exit code 1 with little output. "
                    "This likely means grep found no matches, NOT that tests failed. "
                    "Run pytest directly to see actual test results."
                )

        return warnings

    def _is_piped_to_grep(self, command: str) -> bool:
        """Check if command pipes output to grep.

        Strict matching: must have | grep pattern.
        """
        # Match: cmd | grep, cmd | grep -E, cmd 2>&1 | grep
        # The pipe must be followed by grep (possibly with options)
        return bool(re.search(r"\|\s*grep\b", command))

    def _is_git_diff_command(self, command: str) -> bool:
        """Check if command is a git diff command."""
        patterns = [
            r"\bgit\s+diff\b",
        ]
        return any(re.search(p, command) for p in patterns)

    def _is_patch_command(self, command: str) -> bool:
        """Check if command is a patch or git apply command."""
        patterns = [
            r"\bgit\s+apply\b",
            r"^patch\b",
            r"&&\s*patch\b",
            r";\s*patch\b",
        ]
        return any(re.search(p, command) for p in patterns)

    def _analyze_git_diff(self, command: str, output: str, exit_code: int) -> List[str]:
        """Analyze git diff output for issues. Returns list of warnings."""
        warnings = []

        # 1. Detect git diff run in non-git directory (shows usage message)
        if "usage: git diff" in output.lower():
            warnings.append(
                "git diff was run in a non-git directory or with invalid arguments. "
                "The output shows usage help instead of actual diff. "
                "Make sure to run 'git diff' from within the repository (e.g., 'cd /app && git diff')."
            )

        # 2. Detect empty git diff output (no changes)
        # Only warn if exit code is 0 (successful) and output is empty
        # Don't warn if redirecting to file (> or >>)
        if exit_code == 0 and len(output.strip()) == 0:
            if ">" not in command:  # Not redirecting to file
                warnings.append(
                    "git diff returned empty output - no changes detected. "
                    "This could mean: (1) No files were modified, (2) Changes were already staged with 'git add', "
                    "or (3) The file path is incorrect. "
                    "Use 'git status' to check the state of the repository."
                )

        # 3. Detect "fatal:" errors from git
        if re.search(r"^fatal:", output, re.MULTILINE):
            # Extract the fatal error message
            fatal_match = re.search(r"^fatal:.*$", output, re.MULTILINE)
            if fatal_match:
                warnings.append(
                    f"git encountered a fatal error: {fatal_match.group(0)}. "
                    "Check that you're in the correct directory and the file paths are correct."
                )

        return warnings

    def _analyze_patch(self, command: str, output: str, exit_code: int) -> List[str]:
        """Analyze patch/git apply output for issues. Returns list of warnings."""
        warnings = []

        # 1. Detect patch application failure
        failure_patterns = [
            r"does not apply",
            r"patch failed",
            r"FAILED",
            r"can\'t find file to patch",
            r"Hunk.*FAILED",
            r"error:.*patch",
        ]
        for pattern in failure_patterns:
            if re.search(pattern, output, re.IGNORECASE):
                warnings.append(
                    "Patch application failed. This usually means: "
                    "(1) The file content has changed since the patch was created, "
                    "(2) The patch context doesn't match the current file, or "
                    "(3) The file path in the patch is incorrect. "
                    "Re-read the target file with 'view_file' and create a fresh patch."
                )
                break

        # 2. Detect offset warnings (patch applied but at different location)
        if re.search(r"Hunk.*succeeded.*offset", output, re.IGNORECASE):
            warnings.append(
                "Patch was applied with offset - the context was found at a different "
                "line number than expected. This may indicate the patch was created "
                "from a different version of the file. Verify the changes are correct."
            )

        # 3. Detect reverse patch warning
        if re.search(
            r"Reversed.*patch detected|Already applied", output, re.IGNORECASE
        ):
            warnings.append(
                "The patch appears to have been already applied or is being applied in reverse. "
                "Check if the changes were already made previously."
            )

        return warnings
