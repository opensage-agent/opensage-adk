"""CodeQL static analysis mixin."""

from __future__ import annotations

import logging
import os
import tempfile
import time

from opensage.sandbox.base_sandbox import BaseSandbox
from opensage.sandbox.initializers.base import SandboxInitializer
from opensage.utils.merge_joern_codeql import insert_codeql_results_to_cpg

logger = logging.getLogger(__name__)


class CodeQLInitializer(SandboxInitializer):
    """Initializer that initializes CodeQL static analysis capabilities to sandboxes."""

    async def _async_initialize_impl(
        self: BaseSandbox, all_sandboxes: dict[str, BaseSandbox]
    ) -> bool:
        """Initialize CodeQL environment (async version).

        Raises:
          RuntimeError: Raised when this operation fails."""
        from opensage.session.opensage_session import get_opensage_session

        assert isinstance(self, BaseSandbox)
        assert "neo4j" in all_sandboxes

        logger.info(
            f"Async creating CodeQL environment for session {self.opensage_session_id}..."
        )

        opensage_session = get_opensage_session(self.opensage_session_id)
        try:
            t0 = time.monotonic()
            msg, err = self.run_command_in_container(
                [
                    "bash",
                    "/sandbox_scripts/callgraph/run_codeql.sh",
                    opensage_session.config.build.compile_command,
                ],
                timeout=3600,
            )
            elapsed = time.monotonic() - t0
            logger.info(
                f"CodeQL analysis completed in {elapsed:.1f}s (exit_code={err})"
            )
            if err != 0:
                logger.error(f"CodeQL run failed (exit_code={err}), output:\n{msg}")
                raise RuntimeError(f"CodeQL run failed (exit_code={err})")

            # Always create nodes from CodeQL results
            # If Joern exists, wait for it to be ready first (for potential merging)
            create_not_found = True
            if all_sandboxes.get("joern"):
                await all_sandboxes["joern"].wait_for_ready_or_error()

            if not await all_sandboxes["neo4j"].wait_for_ready_or_error():
                logger.error(f"CodeQL initialization failed: Neo4j sandbox error")
                return False
            neo4j_client = await opensage_session.neo4j.get_async_client("analysis")

            with tempfile.TemporaryDirectory() as tmpdir:
                for res_file in ["results.csv", "fp_accesses.csv", "expr_calls.csv"]:
                    self.copy_file_from_container(
                        f"/shared/out/callgraph/{res_file}",
                        os.path.join(tmpdir, res_file),
                    )

                await insert_codeql_results_to_cpg(
                    neo4j_client, tmpdir, create_not_found_nodes=create_not_found
                )
            return True
        except Exception as e:
            logger.error(f"CodeQL initialization failed: {e}")
            return False
