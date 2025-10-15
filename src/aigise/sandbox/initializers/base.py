"""Base Initializer class for sandbox functionality."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from aigise.session.sandbox_state import SandboxState

logger = logging.getLogger(__name__)


class SandboxInitializer(ABC):
    """Base class for sandbox functionality initializers."""

    @abstractmethod
    async def async_initialize(self) -> None:
        """
        Initialize initializer-specific functionality (asynchronous version).

        This method is called after the sandbox backend is initialized
        and should set up any tool-specific environment or dependencies.
        Use this for operations that require async I/O.
        """
        pass

    @abstractmethod
    async def ensure_ready(self) -> None:
        """Ensure the sandbox is ready."""
        pass


class DefaultInitializer(SandboxInitializer):
    """Default initializer with no special initialization."""

    async def async_initialize(self) -> None:
        """Wait for MCP server to be ready."""

        await self.ensure_ready()

    async def ensure_ready(self) -> None:
        from aigise.session import get_aigise_session
        from aigise.utils.agent_utils import get_mcp_url_from_session_id

        aigise_session = get_aigise_session(self.aigise_session_id)
        aigise_session.sandboxes._sandboxes[self.sandbox_type] = self

        async def verify_connection(url: str) -> bool:
            """Check if MCP SSE server is ready by testing initial response."""
            import httpx

            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    async with client.stream("GET", url) as response:
                        # If we can receive response (status code + headers), server is ready
                        return response.status_code == 200
                        # Don't read body, exit context manager to auto-close connection
            except Exception:
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
