"""Main sandbox initializer."""

from __future__ import annotations

import logging

from opensage.sandbox.base_sandbox import BaseSandbox, SandboxState
from opensage.sandbox.initializers.base import SandboxInitializer

logger = logging.getLogger(__name__)


class MainInitializer(SandboxInitializer):
    """Initializer that initializes main sandbox."""

    async def _async_initialize_impl(
        self: BaseSandbox, all_sandboxes: dict[str, BaseSandbox]
    ) -> bool:
        """Initialize main sandbox (async version)."""
        assert isinstance(self, BaseSandbox)

        logger.info(
            f"Async initializing main sandbox for session {self.opensage_session_id}..."
        )

        return True
