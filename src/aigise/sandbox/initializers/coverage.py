from __future__ import annotations

import logging

from aigise.sandbox.base_sandbox import BaseSandbox
from aigise.session.sandbox_state import SandboxState

from .base import SandboxInitializer

logger = logging.getLogger(__name__)


class CoverageInitializer(SandboxInitializer):
    """Initializer that initializes Joern code analysis capabilities to sandboxes."""

    async def async_initialize(self) -> None:
        """Initialize Coverage environment (async version)."""
        assert isinstance(self, BaseSandbox)

        logger.info(
            f"Async initializing Coverage environment for session {self.aigise_session_id}..."
        )

        msg, err = self.run_command_in_container(
            command=["bash", "/sandbox_scripts/ossfuzz/compile_coverage.sh"],
            timeout=600,
        )

        if err:
            logger.error(f"Coverage initialization error: {msg}")

        await self.ensure_ready()

    async def ensure_ready(self) -> None:
        from aigise.session.aigise_session import get_aigise_session

        aigise_session = get_aigise_session(self.aigise_session_id)

        aigise_session.sandboxes._sandboxes[self.sandbox_type] = self
        aigise_session.sandboxes.set_sandbox_state(
            self.sandbox_type, SandboxState.READY
        )
