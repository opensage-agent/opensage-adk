"""CodeQL static analysis mixin."""

from __future__ import annotations

import logging
import os
import tempfile

from aigise.sandbox.base_sandbox import BaseSandbox
from aigise.sandbox.initializers.base import SandboxInitializer
from aigise.session.sandbox_state import SandboxState
from aigise.utils.merge_joern_codeql import insert_codeql_results_to_cpg

logger = logging.getLogger(__name__)


class CodeQLInitializer(SandboxInitializer):
    """Initializer that initializes CodeQL static analysis capabilities to sandboxes."""

    async def async_initialize(self) -> None:
        """Initialize CodeQL environment (async version)."""
        from aigise.session.aigise_session import get_aigise_session

        assert isinstance(self, BaseSandbox)

        logger.info(
            f"Async creating CodeQL environment for session {self.aigise_session_id}..."
        )

        aigise_session = get_aigise_session(self.aigise_session_id)
        aigise_session.sandboxes._sandboxes[self.sandbox_type] = self
        try:
            msg, err = self.run_command_in_container(
                [
                    "bash",
                    "/sandbox_scripts/callgraph/run_codeql.sh",
                    aigise_session.config.build.compile_command,
                ],
                timeout=1200,
            )
            if err != 0:
                raise RuntimeError(f"CodeQL run failed: {msg}")

            # Always create nodes from CodeQL results
            # If Joern exists, wait for it to be ready first (for potential merging)
            create_not_found = True
            if "joern" in aigise_session.config.sandbox.sandboxes:
                await aigise_session.sandboxes.wait_for_ready_or_error("joern")

            await aigise_session.sandboxes.wait_for_ready("neo4j")
            neo4j_client = await aigise_session.neo4j.get_async_client("analysis")

            with tempfile.TemporaryDirectory() as tmpdir:
                for res_file in ["results.csv", "fp_accesses.csv", "expr_calls.csv"]:
                    self.copy_file_from_container(
                        f"/shared/out/callgraph/{res_file}",
                        os.path.join(tmpdir, res_file),
                    )

                await insert_codeql_results_to_cpg(
                    neo4j_client, tmpdir, create_not_found_nodes=create_not_found
                )
        except Exception as e:
            logger.error(f"CodeQL initialization failed: {e}")
            raise

        await self.ensure_ready()
