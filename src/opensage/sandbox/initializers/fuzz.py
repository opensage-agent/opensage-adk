"""Fuzzing Initializer."""

from __future__ import annotations

import logging
import re

from opensage.sandbox.base_sandbox import BaseSandbox, SandboxState
from opensage.utils.project_info import PROJECT_PATH

from .base import SandboxInitializer

logger = logging.getLogger(__name__)


class FuzzInitializer(SandboxInitializer):
    """Initializer that initializes fuzzing capabilities to sandboxes."""

    async def _async_initialize_impl(
        self: BaseSandbox, all_sandboxes: dict[str, BaseSandbox]
    ) -> bool:
        """Initialize fuzzing environment (async version)."""

        assert isinstance(self, BaseSandbox)
        assert "main" in all_sandboxes

        logger.info(
            f"Async initializing fuzzing environment for session {self.opensage_session_id}..."
        )

        try:
            # Wait for main sandbox to be ready
            if not await all_sandboxes["main"].wait_for_ready_or_error():
                logger.error(f"Fuzz initialization failed: Main sandbox error")
                return False

            # Extract environment information from arvo script
            res, exit_code = self.run_command_in_container(
                "cat /bin/arvo", timeout=1200
            )
            if exit_code != 0:
                infos = self._extract_infos_from_ossfuzz(all_sandboxes["main"])
            else:
                infos = self._extract_infos_from_arvo_script(res)

            # Compile with AFL++
            await self._compile_with_aflpp(infos)
            return True
        except Exception as e:
            logger.error(f"Failed to initialize fuzzing environment: {e}")
            return False

    def _extract_infos_from_ossfuzz(self, sandbox: BaseSandbox) -> dict[str, str]:
        infos = {}
        for env in ["SANITIZER", "FUZZING_LANGUAGE", "ARCHITECTURE"]:
            res, exit_code = sandbox.run_command_in_container(
                f"echo ${env}", timeout=1200
            )
            if exit_code != 0:
                raise RuntimeError(f"Failed to get {env}: {res}")
            infos[env] = res.strip()
        # find fuzz target from /usr/local/bin/run_poc
        res, exit_code = sandbox.run_command_in_container(
            "cat /usr/local/bin/run_poc", timeout=1200
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to read run_poc script: {res}")
        for line in res.splitlines():
            m = re.match(r"^\s+/out/(\S+)\s+/tmp/poc", line)
            if m:
                infos["FUZZ_TARGET"] = m.group(1)
                break
        return infos

    def _extract_infos_from_arvo_script(self, arvo_script: str) -> dict[str, str]:
        """Extract information from arvo script."""
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

    async def _compile_with_aflpp(self, infos: dict[str, str]) -> None:
        """Compile the project with AFL++.

        Raises:
          RuntimeError: Raised when this operation fails."""
        logger.info("Compiling with AFL++...")

        # Set environment variables and run compilation
        env_cmd = f"export SANITIZER={infos['SANITIZER']} && export FUZZING_LANGUAGE={infos['FUZZING_LANGUAGE']} && export ARCHITECTURE={infos['ARCHITECTURE']} && bash /sandbox_scripts/ossfuzz/compile_aflpp.sh"

        msg, err = self.run_command_in_container(env_cmd, timeout=3600)

        if err != 0:
            logger.info("Recovering old build files...")
            self.run_command_in_container(
                "rm -rf /out && mv /out.bak /out", timeout=1200
            )
            raise RuntimeError(f"AFL++ compilation failed: {msg}")

        logger.info("AFL++ compilation completed successfully")
