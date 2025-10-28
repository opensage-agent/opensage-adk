"""Joern code analysis mixin."""

from __future__ import annotations

import logging

from aigise.sandbox.base_sandbox import BaseSandbox
from aigise.session.joern_client import JoernClient
from aigise.session.sandbox_state import SandboxState
from aigise.utils.merge_joern_codeql import import_joern_cpg, update_joern_cpg

from .base import SandboxInitializer

logger = logging.getLogger(__name__)


class JoernInitializer(SandboxInitializer):
    """Initializer that initializes Joern code analysis capabilities to sandboxes."""

    async def async_initialize(self) -> None:
        """Initialize Joern environment (async version)."""
        from aigise.session.aigise_session import get_aigise_session

        assert isinstance(self, BaseSandbox)

        logger.info(
            f"Async initializing Joern environment for session {self.aigise_session_id}..."
        )

        aigise_session = get_aigise_session(self.aigise_session_id)
        aigise_session.sandboxes._sandboxes[self.sandbox_type] = self

        await aigise_session.sandboxes.wait_for_ready("main")

        main_sandbox = aigise_session.sandboxes.get_sandbox("main")

        msg, err = self.run_command_in_container(
            ["bash", "/sandbox_scripts/callgraph/init.sh"]
        )
        if err != 0:
            raise RuntimeError(f"Joern init failed: {msg}")

        if err != 0:
            raise RuntimeError(f"Joern code copy failed: {msg}")

        msg, err = self.run_command_in_container(
            ["bash", "/sandbox_scripts/callgraph/run_joern.sh"]
        )
        if err != 0:
            raise RuntimeError(f"Joern run failed: {msg}")

        msg, err = main_sandbox.run_command_in_container(
            ["pip3", "install", "networkx"]
        )
        if err != 0:
            raise RuntimeError(f"Joern networkx install failed: {msg}")

        # wait for neo4j to be ready, such that we can import the CPG
        await aigise_session.sandboxes.wait_for_ready("neo4j")
        neo4j_client = await aigise_session.neo4j.get_async_client("analysis")

        msg, err = main_sandbox.run_command_in_container(
            [
                "python3",
                "/sandbox_scripts/callgraph/update_graphml.py",
                "/shared/out/callgraph/joern_export.xml",
                "/shared/neo4j/import/joern_export.xml",
            ]
        )
        if err != 0:
            raise RuntimeError(f"Joern graphml update failed: {msg}")

        await import_joern_cpg(neo4j_client, "/joern_export.xml")
        await update_joern_cpg(neo4j_client, fix_identical_methods=True)

        client = JoernClient(
            server_endpoint=f"{aigise_session.config.default_host}:18087"
        )

        await client.aexecute("importCpg('/cpg.bin')")

        await self.ensure_ready()

    async def ensure_ready(self) -> None:
        from aigise.session.aigise_session import get_aigise_session

        aigise_session = get_aigise_session(self.aigise_session_id)

        aigise_session.sandboxes._sandboxes[self.sandbox_type] = self
        aigise_session.sandboxes.set_sandbox_state(
            self.sandbox_type, SandboxState.READY
        )
