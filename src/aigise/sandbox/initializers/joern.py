"""Joern code analysis mixin."""

from __future__ import annotations

import asyncio
import logging
import tempfile

import networkx as nx

from aigise.sandbox.base_sandbox import BaseSandbox
from aigise.session.joern_client import JoernClient
from aigise.session.sandbox_state import SandboxState
from aigise.utils.merge_joern_codeql import (
    import_joern_callgraph,
    update_joern_cpg,
)

from .base import SandboxInitializer

logger = logging.getLogger(__name__)


def _update_graphml(graphml_path: str, output_path: str):
    graph = nx.read_graphml(graphml_path)
    # change labelV to labels
    for node in graph.nodes(data=True):
        if "labelV" in node[1]:
            node[1]["labels"] = ":" + node[1].pop("labelV")

    # change labelE to label
    for u, v, data in graph.edges(data=True):
        if "labelE" in data:
            data["label"] = data.pop("labelE")

    nx.write_graphml(graph, output_path, named_key_ids=True)


class JoernInitializer(SandboxInitializer):
    """Initializer that initializes Joern code analysis capabilities to sandboxes."""

    async def async_initialize(self) -> None:
        """Initialize Joern environment (async version)."""
        from aigise.session.aigise_session import get_aigise_session

        assert isinstance(self, BaseSandbox)

        logger.info(
            f"Async creating Joern environment for session {self.aigise_session_id}..."
        )

        aigise_session = get_aigise_session(self.aigise_session_id)
        aigise_session.sandboxes._sandboxes[self.sandbox_type] = self

        # await aigise_session.sandboxes.wait_for_ready("main")

        try:
            # Wrap Joern initialization with 10-minute timeout
            await asyncio.wait_for(
                self._initialize_joern_with_timeout(aigise_session),
                timeout=1200.0,  # 10 minutes
            )
        except asyncio.TimeoutError:
            logger.error(
                f"Joern initialization failed; timed out after 10 minutes for session {self.aigise_session_id}"
            )
            raise
        except Exception as e:
            logger.error(f"Joern initialization failed: {e}")
            raise

        await self.ensure_ready()

    async def _initialize_joern_with_timeout(self, aigise_session) -> None:
        """Execute Joern initialization steps with timeout protection."""
        msg, err = self.run_command_in_container(
            ["bash", "/sandbox_scripts/callgraph/init.sh"],
            timeout=1200,
        )
        if err != 0:
            raise RuntimeError(f"Joern init failed: {msg}")

        if err != 0:
            raise RuntimeError(f"Joern code copy failed: {msg}")

        msg, err = self.run_command_in_container(
            [
                "bash",
                "/sandbox_scripts/callgraph/run_joern.sh",
                aigise_session.config.src_dir_in_sandbox,
            ],
            timeout=1200,
        )
        if err != 0:
            raise RuntimeError(f"Joern run failed: {msg}")

        # wait for neo4j to be ready, such that we can import the CPG
        await aigise_session.sandboxes.wait_for_ready("neo4j")
        neo4j_client = await aigise_session.neo4j.get_async_client("analysis")

        await import_joern_callgraph(neo4j_client, "/")
        await update_joern_cpg(neo4j_client, fix_identical_methods=True)

        client = JoernClient(
            server_endpoint=f"{aigise_session.config.default_host}:18087"
        )

        await client.aexecute("importCpg('/cpg.bin')")
