"""CodeQL static analysis mixin."""

from __future__ import annotations

import os
import tempfile

from loguru import logger

from aigise.sandbox.base_sandbox import BaseSandbox
from aigise.sandbox.initializers.base import SandboxInitializer
from aigise.session.sandbox_state import SandboxState
from aigise.utils.merge_joern_codeql import insert_codeql_results_to_cpg


class CodeQLInitializer(SandboxInitializer):
    """Initializer that initializes CodeQL static analysis capabilities to sandboxes."""

    async def async_initialize(self) -> None:
        """Initialize CodeQL environment (async version)."""
        from aigise.session.aigise_session import get_aigise_session

        assert isinstance(self, BaseSandbox)

        logger.info(
            f"Async initializing CodeQL environment for session {self.aigise_session_id}..."
        )

        aigise_session = get_aigise_session(self.aigise_session_id)

        await aigise_session.sandboxes.wait_for_ready("joern")

        msg, err = self.run_command_in_container(
            [
                "bash",
                "/shared/callgraph/run_codeql.sh",
                aigise_session.config.build.compile_command,
            ]
        )
        if err != 0:
            raise RuntimeError(f"CodeQL run failed: {msg}")

        await aigise_session.sandboxes.wait_for_ready("neo4j")
        neo4j_client = await aigise_session.neo4j.get_async_client("analysis")

        with tempfile.TemporaryDirectory() as tmpdir:
            for res_file in ["results.csv", "fp_accesses.csv", "expr_calls.csv"]:
                self.copy_file_from_container(
                    f"/shared/out/callgraph/{res_file}", os.path.join(tmpdir, res_file)
                )

            await insert_codeql_results_to_cpg(neo4j_client, tmpdir)

        await self.ensure_ready()

    async def ensure_ready(self) -> None:
        from aigise.session.aigise_session import get_aigise_session

        aigise_session = get_aigise_session(self.aigise_session_id)

        aigise_session.sandboxes._sandboxes[self.sandbox_type] = self
        aigise_session.sandboxes.set_sandbox_state(
            self.sandbox_type, SandboxState.READY
        )
