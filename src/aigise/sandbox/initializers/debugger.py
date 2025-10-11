"""Debugger Initializer."""

from __future__ import annotations

from loguru import logger

from .base import SandboxInitializer


class DebuggerInitializer(SandboxInitializer):
    """Initializer that initializes debugger capabilities to sandboxes."""

    async def async_initialize(self) -> None:
        """Initialize debugger environment (async version)."""
        logger.info(
            f"Async initializing debugger environment for session {self.aigise_session_id}..."
        )
        # TODO: Add async debugger setup (downloads, network operations) here
        pass
