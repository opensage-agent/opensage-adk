from __future__ import annotations

import logging

from aigise.sandbox.base_sandbox import BaseSandbox
from aigise.session.sandbox_state import SandboxState

from .base import SandboxInitializer

logger = logging.getLogger(__name__)


class CoverageInitializer(SandboxInitializer):
    """Initializer that initializes coverage capabilities to sandboxes."""

    async def async_initialize(self) -> None:
        """Initialize Coverage environment (async version)."""
        assert isinstance(self, BaseSandbox)

        logger.info(
            f"Async initializing Coverage environment for session {self.aigise_session_id}..."
        )

        msg, err = self.run_command_in_container(
            command=["bash", "/sandbox_scripts/ossfuzz/compile_coverage.sh"],
            timeout=3600,
        )

        if err:
            logger.error(f"Coverage initialization error: {msg}")
            raise RuntimeError(f"Coverage environment initialization failed: {msg}")

        await self.ensure_ready()

    async def ensure_ready(self) -> None:
        """Verify coverage sandbox has Python 3.12 and required Python packages.

        Coverage bash_tools upload script runs inside the coverage sandbox and
        depends on:
        - Python 3.12 (provided by /app/.venv via uv)
        - `neo4j` driver
        - `msgspec` (LLVM coverage JSON parser)
        """
        from aigise.session import get_aigise_session

        assert isinstance(self, BaseSandbox)

        logger.info(
            "Verifying Python environment in coverage sandbox for session %s...",
            self.aigise_session_id,
        )

        aigise_session = get_aigise_session(self.aigise_session_id)
        aigise_session.sandboxes._sandboxes[self.sandbox_type] = self

        msg, err = self.run_command_in_container(["python3", "--version"])
        if err != 0:
            raise RuntimeError(f"python3 not available in coverage sandbox: {msg}")
        if "3.12" not in msg:
            raise RuntimeError(
                f"coverage sandbox python3 is not 3.12 (got: {msg.strip()})"
            )

        msg, err = self.run_command_in_container(
            ["python3", "-c", "import neo4j, msgspec; print('deps ok')"],
        )
        if err != 0:
            raise RuntimeError(
                "Missing Python deps in coverage sandbox. "
                "Expected neo4j + msgspec installed in image. "
                f"Error: {msg}"
            )

        aigise_session.sandboxes.set_sandbox_state(
            self.sandbox_type, SandboxState.READY
        )
        logger.info(
            "Coverage sandbox successfully initialized for session %s",
            self.aigise_session_id,
        )
