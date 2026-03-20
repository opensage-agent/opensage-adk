from __future__ import annotations

import logging

from opensage.sandbox.base_sandbox import BaseSandbox

from .base import SandboxInitializer

logger = logging.getLogger(__name__)


class GDBDebuggerInitializer(SandboxInitializer):
    """Initializer for debugger sandboxes to compile debug binaries."""

    async def _async_initialize_impl(
        self: BaseSandbox, all_sandboxes: dict[str, BaseSandbox]
    ) -> bool:
        """Run the debug compilation script inside the sandbox."""
        assert isinstance(self, BaseSandbox)

        logger.info(
            "Async initializing debugger sandbox for session %s...",
            self.opensage_session_id,
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
            logger.error("Debugger compilation failed: %s", msg)
            return False
        logger.info("Debugger compilation completed successfully.")
        return True
