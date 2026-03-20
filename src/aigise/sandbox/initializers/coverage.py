from __future__ import annotations

import logging

from opensage.sandbox.base_sandbox import BaseSandbox, SandboxState

from .base import SandboxInitializer

logger = logging.getLogger(__name__)


class CoverageInitializer(SandboxInitializer):
    """Initializer that initializes coverage capabilities to sandboxes."""

    async def _async_initialize_impl(
        self: BaseSandbox, all_sandboxes: dict[str, BaseSandbox]
    ) -> bool:
        """Initialize Coverage environment (async version)."""
        assert isinstance(self, BaseSandbox)

        logger.info(
            f"Async initializing Coverage environment for session {self.opensage_session_id}..."
        )

        msg, err = self.run_command_in_container(
            command=["bash", "/sandbox_scripts/ossfuzz/compile_coverage.sh"],
            timeout=3600,
        )

        if err:
            logger.error(f"Coverage initialization error: {msg}")
            return False

        return True

    async def _ensure_ready_impl(self: BaseSandbox) -> bool:
        """Verify coverage sandbox has Python 3.12 and required Python packages.

        Coverage bash_tools upload script runs inside the coverage sandbox and
        depends on:
        - Python 3.12 (provided by /app/.venv via uv)
        - `neo4j` driver
        - `msgspec` (LLVM coverage JSON parser)
        """
        assert isinstance(self, BaseSandbox)

        logger.info(
            "Verifying Python environment in coverage sandbox for session %s...",
            self.opensage_session_id,
        )

        msg, err = self.run_command_in_container(["python3", "--version"])
        if err != 0:
            logger.error(f"python3 not available in coverage sandbox: {msg}")
            return False
        if "3.12" not in msg:
            logger.error(f"coverage sandbox python3 is not 3.12 (got: {msg.strip()})")
            return False

        msg, err = self.run_command_in_container(
            ["python3", "-c", "import neo4j, msgspec; print('deps ok')"],
        )
        if err != 0:
            logger.error(
                "Missing Python deps in coverage sandbox. "
                "Expected neo4j + msgspec installed in image. "
                f"Error: {msg}"
            )
            return False
        return True
