"""Fuzzing Initializer."""

from __future__ import annotations

import logging
import os
import tempfile

from aigise.sandbox.base_sandbox import BaseSandbox
from aigise.session.sandbox_state import SandboxState
from aigise.utils.project_info import PROJECT_PATH

from .base import SandboxInitializer

logger = logging.getLogger(__name__)


class FuzzInitializer(SandboxInitializer):
    """Initializer that initializes fuzzing capabilities to sandboxes."""

    async def async_initialize(self) -> None:
        """Initialize fuzzing environment (async version)."""
        from aigise.session.aigise_session import get_aigise_session

        assert isinstance(self, BaseSandbox)

        logger.info(
            f"Async initializing fuzzing environment for session {self.aigise_session_id}..."
        )

        aigise_session = get_aigise_session(self.aigise_session_id)
        aigise_session.sandboxes._sandboxes[self.sandbox_type] = self

        # Wait for main sandbox to be ready
        await aigise_session.sandboxes.wait_for_ready("main")

        # Extract environment information from arvo script
        res, exit_code = self.run_command_in_container("cat /bin/arvo")
        if exit_code != 0:
            raise RuntimeError(f"Failed to read arvo script: {res}")
        infos = self._extract_infos_from_arvo_script(res)

        # Set up fuzzing environment
        await self._setup_fuzzing_environment(infos)

        # Compile with AFL++
        await self._compile_with_aflpp(infos)

        await self.ensure_ready()

    def _extract_infos_from_arvo_script(self, arvo_script: str) -> dict[str, str]:
        """Extract information from arvo script."""
        import re

        infos = {}
        # find 'export XXX=YYYY' in arvo_script
        env_names = ["SANITIZER", "FUZZING_LANGUAGE", "ARCHITECTURE"]
        for line in arvo_script.splitlines():
            for env_name in env_names:
                if line.startswith(f"export {env_name}="):
                    infos[env_name] = line.split("=", 1)[1].strip().strip('"')

        # find first appearance of "   /out/{fuzz_target} /tmp/poc"
        for line in arvo_script.splitlines():
            m = re.match(r"^\s+/out/(\S+)\s+/tmp/poc", line)
            if m:
                infos["FUZZ_TARGET"] = m.group(1)
                break
        return infos

    async def _setup_fuzzing_environment(self, infos: dict[str, str]) -> None:
        """Set up the fuzzing environment."""
        logger.info("Setting up fuzzing environment...")

        # Copy source code from /shared/code to /src for compilation
        logger.info("Copying source code from /shared/code to /src...")
        copy_cmd = "cp -r /shared/code/* /src/"
        msg, err = self.run_command_in_container(copy_cmd)
        if err != 0:
            raise RuntimeError(f"Failed to copy source code: {msg}")

        logger.info(f"Fuzzing environment verified: {infos}")

    async def _compile_with_aflpp(self, infos: dict[str, str]) -> None:
        """Compile the project with AFL++."""
        logger.info("Compiling with AFL++...")

        # Set environment variables and run compilation
        env_cmd = f"export SANITIZER={infos['SANITIZER']} && export FUZZING_LANGUAGE={infos['FUZZING_LANGUAGE']} && export ARCHITECTURE={infos['ARCHITECTURE']} && bash /sandbox_scripts/ossfuzz/compile_aflpp.sh"

        msg, err = self.run_command_in_container(env_cmd)

        if err != 0:
            raise RuntimeError(f"AFL++ compilation failed: {msg}")

        logger.info("AFL++ compilation completed successfully")

    async def ensure_ready(self) -> None:
        """Ensure the sandbox is ready."""
        from aigise.session.aigise_session import get_aigise_session

        aigise_session = get_aigise_session(self.aigise_session_id)
        # register once here since when using cache, we only call ensure_ready
        aigise_session.sandboxes._sandboxes[self.sandbox_type] = self
        aigise_session.sandboxes.set_sandbox_state(
            self.sandbox_type, SandboxState.READY
        )
