from __future__ import annotations

import logging

from aigise.sandbox.base_sandbox import BaseSandbox

from .base import SandboxInitializer

logger = logging.getLogger(__name__)


class DebuggerInitializer(SandboxInitializer):
    """Initializer for debugger sandboxes to compile debug binaries."""

    async def async_initialize(self) -> None:
        """Run the debug compilation script inside the sandbox."""
        assert isinstance(self, BaseSandbox)

        logger.info(
            "Async initializing debugger sandbox for session %s...",
            self.aigise_session_id,
        )

        msg, err = self.run_command_in_container(
            command=["bash", "/sandbox_scripts/ossfuzz/compile_debug.sh"],
            timeout=3600,
        )
        if err:
            logger.error("Debugger compilation failed: %s", msg)
            logger.info("Recovering old build files...")
            self.run_command_in_container(
                "rm -rf /out && mv /out.bak /out", timeout=1200
            )
            raise RuntimeError("Debugger compilation failed")
        else:
            logger.info("Debugger compilation completed successfully.")

        await self.ensure_ready()
