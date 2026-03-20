"""Base Initializer class for sandbox functionality."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC

from opensage.sandbox.base_sandbox import BaseSandbox, SandboxState

logger = logging.getLogger(__name__)


async def _verify_mcp_sse_ready(url: str) -> bool:
    """Return True iff MCP SSE endpoint is connectable."""
    from mcp.client.sse import sse_client

    try:
        async with asyncio.timeout(10.0):
            async with sse_client(url, timeout=5.0, sse_read_timeout=10.0) as (
                _read,
                _write,
            ):
                return True
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug("MCP connection verify failed for %s: %s", url, exc)
        return False


class SandboxInitializer(ABC):
    """Base class for sandbox functionality initializers."""

    async def _async_initialize_impl(
        self: BaseSandbox, all_sandboxes: dict[str, BaseSandbox]
    ) -> bool:
        # custom implementation for the initializer
        return True

    async def async_initialize(
        self: BaseSandbox, all_sandboxes: dict[str, BaseSandbox]
    ) -> None:
        """Initialize sandbox initializer (async version).

        Raises:
          RuntimeError: Raised when this operation fails."""
        initialized = await self._async_initialize_impl(all_sandboxes)
        if not initialized:
            self.state = SandboxState.ERROR
            raise RuntimeError("Sandbox initialization failed")
        await self.ensure_ready()

    async def _check_mcp_connections(
        self: BaseSandbox, mcp_services: list[str]
    ) -> None:
        """Wait for all declared MCP SSE services to be ready.

        This helper is intended to be called by subclasses that override
        `_ensure_ready_impl()` but still want the standard MCP readiness checks.

        Args:
            mcp_services (list[str]): List of MCP service names. Each name must exist in
                `OpenSageConfig.mcp.services` for the session.
        Raises:
            RuntimeError: If MCP URL resolution fails (e.g. missing config).
        """
        if not mcp_services:
            return

        from opensage.utils.agent_utils import get_mcp_url_from_session_id

        for mcp_name in mcp_services:
            url = get_mcp_url_from_session_id(mcp_name, self.opensage_session_id)
            retry_num = 0
            logger.info("Waiting for MCP server %s at %s...", mcp_name, url)
            while not await _verify_mcp_sse_ready(url):
                retry_num += 1
                logger.info("Still waiting for %s... (retry %d)", mcp_name, retry_num)
                await asyncio.sleep(1)
            logger.info("MCP server %s is ready!", mcp_name)

    async def _ensure_ready_impl(self: BaseSandbox) -> bool:
        """Default readiness check.

        If the sandbox config declares MCP dependencies via
        `container_config.mcp_services`, wait until those MCP services are ready.
        """
        mcp_services = getattr(self.container_config_obj, "mcp_services", None) or []
        await self._check_mcp_connections(mcp_services)
        return True

    async def ensure_ready(self: BaseSandbox) -> None:
        ready = await self._ensure_ready_impl()
        if not ready:
            self.state = SandboxState.ERROR
            raise RuntimeError("Sandbox health check failed")

        self.state = SandboxState.READY
        logger.info(
            "Sandbox '%s' is READY for session %s",
            getattr(self, "sandbox_type", "<unknown>"),
            getattr(self, "opensage_session_id", "<unknown>"),
        )
