"""Base Initializer class for sandbox functionality."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from aigise.session.sandbox_state import SandboxState

logger = logging.getLogger(__name__)


class SandboxInitializer(ABC):
    """Base class for sandbox functionality initializers."""

    async def async_initialize(self) -> None:
        """Initialize sandbox initializer (async version)."""

        await self.ensure_ready()

    async def ensure_ready(self) -> None:
        from aigise.session import get_aigise_session
        from aigise.utils.agent_utils import get_mcp_url_from_session_id

        aigise_session = get_aigise_session(self.aigise_session_id)
        aigise_session.sandboxes._sandboxes[self.sandbox_type] = self

        async def verify_connection(url: str) -> bool:
            """Check if MCP SSE server is ready by establishing a real connection."""
            from mcp.client.sse import sse_client

            try:
                # Use real MCP client for proper connection and cleanup
                async with asyncio.timeout(10.0):
                    async with sse_client(url, timeout=5.0, sse_read_timeout=10.0) as (
                        read,
                        write,
                    ):
                        # Successfully connected and initialized
                        return True
            except Exception as e:
                logger.debug(f"MCP connection verify failed for {url}: {e}")
                return False

        try:
            url = get_mcp_url_from_session_id(self.sandbox_type, self.aigise_session_id)
            retry_num = 0
            logger.info(f"Waiting for MCP server {self.sandbox_type} at {url}...")

            while not await verify_connection(url):
                retry_num += 1
                logger.info(
                    f"Still waiting for {self.sandbox_type}... (retry {retry_num})"
                )
                await asyncio.sleep(1)

            logger.info(f"MCP server {self.sandbox_type} is ready!")
        except (RuntimeError, AttributeError):
            logger.debug(
                f"{self.sandbox_type} is not an MCP server, skipping connection check"
            )
        aigise_session.sandboxes.set_sandbox_state(
            self.sandbox_type, SandboxState.READY
        )
        logger.info(
            f"main environment successfully initialized for session {self.aigise_session_id}"
        )
