"""Main sandbox initializer."""

from __future__ import annotations

import logging

from aigise.sandbox.base_sandbox import BaseSandbox
from aigise.sandbox.initializers.base import SandboxInitializer
from aigise.session.sandbox_state import SandboxState

logger = logging.getLogger(__name__)


class MainInitializer(SandboxInitializer):
    """Initializer that installs required Python packages for main sandbox."""

    async def ensure_ready(self) -> None:
        """Verify neo4j package is available in main sandbox.

        Note: The neo4j package should be installed in the Dockerfile,
        so this method only verifies it's available.
        """
        from aigise.session import get_aigise_session

        assert isinstance(self, BaseSandbox)

        logger.info(
            f"Verifying neo4j package in main sandbox for session {self.aigise_session_id}..."
        )

        aigise_session = get_aigise_session(self.aigise_session_id)
        aigise_session.sandboxes._sandboxes[self.sandbox_type] = self

        # Verify neo4j package is available.
        #
        # The main sandbox image creates /app/.venv and prepends it to PATH, so
        # `python3` should resolve to the venv interpreter and import installed deps.
        logger.info("Verifying neo4j package is importable via python3...")
        msg, err = self.run_command_in_container(
            ["python3", "-c", "import neo4j; print('neo4j package is available')"],
        )

        if err != 0:
            logger.error(f"Failed to verify neo4j package: {msg}")
            raise RuntimeError(
                f"neo4j package is not available. It should be installed in the Dockerfile. Error: {msg}"
            )

        logger.info("neo4j package verified successfully")

        aigise_session.sandboxes.set_sandbox_state(
            self.sandbox_type, SandboxState.READY
        )
        logger.info(
            f"Main sandbox successfully initialized for session {self.aigise_session_id}"
        )
