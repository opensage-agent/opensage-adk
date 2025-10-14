"""Neo4j Initializer."""

from __future__ import annotations

import asyncio

from loguru import logger

from aigise.sandbox.initializers.base import SandboxInitializer
from aigise.session.sandbox_state import SandboxState


class Neo4jInitializer(SandboxInitializer):
    """Initializer that initializes Neo4j code analysis capabilities to sandboxes."""

    async def async_initialize(self) -> None:
        """Initialize Neo4j environment (async version)."""
        await self.ensure_ready()

    async def ensure_ready(self) -> None:
        from aigise.session.aigise_session import get_aigise_session

        logger.info(
            f"Async initializing Neo4j environment for session {self.aigise_session_id}..."
        )
        aigise_session = get_aigise_session(self.aigise_session_id)
        aigise_session.sandboxes._sandboxes[self.sandbox_type] = self
        self.neo4j_client = aigise_session.neo4j.get_async_client_without_connection(
            "default"
        )
        while not await self.neo4j_client.verify_connection():
            await asyncio.sleep(1)

        aigise_session.sandboxes.set_sandbox_state(
            self.sandbox_type, SandboxState.READY
        )
