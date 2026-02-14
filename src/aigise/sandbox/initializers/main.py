"""Main sandbox initializer."""

from __future__ import annotations

import logging

from aigise.sandbox.base_sandbox import BaseSandbox, SandboxState
from aigise.sandbox.initializers.base import SandboxInitializer

logger = logging.getLogger(__name__)


class MainInitializer(SandboxInitializer):
    """Initializer that initializes main sandbox."""

    async def _async_initialize_impl(
        self: BaseSandbox, all_sandboxes: dict[str, BaseSandbox]
    ) -> bool:
        """Initialize main sandbox (async version)."""
        assert isinstance(self, BaseSandbox)

        logger.info(
            f"Async initializing main sandbox for session {self.aigise_session_id}..."
        )

        return True
