"""Joern code analysis mixin."""

from __future__ import annotations

import asyncio
import logging
import shlex
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

        # Write Joern server host to ~/.bashrc
        self._write_joern_env_to_bashrc(aigise_session)

        await self.ensure_ready()

    async def _initialize_joern_with_timeout(self, aigise_session) -> None:
        """Execute Joern initialization steps with timeout protection."""
        msg, err = self.run_command_in_container(
            ["bash", "/sandbox_scripts/callgraph/init.sh"],
            timeout=3600,
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
            timeout=3600,
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

    def _write_joern_env_to_bashrc(self, aigise_session) -> None:
        """Write Joern server host environment variable to /shared/bashrc."""
        assert isinstance(self, BaseSandbox)

        # Get this container's IP address
        msg, err = self.run_command_in_container(["hostname", "-I"])
        if err != 0 or not msg.strip():
            logger.warning("Failed to get container IP, using fallback host")
            joern_host = "127.0.0.1"
        else:
            # hostname -I returns space-separated IPs, take the first one
            joern_host = msg.strip().split()[0]

        # Joern server listens on port 8081 inside the container
        # (port 18087 is the host mapping, not the container port)
        joern_port = 8081

        # Escape values for safe use in bash script
        joern_host_escaped = shlex.quote(joern_host)
        joern_port_escaped = shlex.quote(str(joern_port))

        # Create bash script to append to /shared/bashrc (avoid duplicates)
        bash_script = f"""
# Ensure /shared directory exists
mkdir -p /shared

# Check if Joern env vars already exist
if ! grep -q "export JOERN_SERVER_HOST=" /shared/bashrc 2>/dev/null; then
    echo '' >> /shared/bashrc
    echo '# Joern server settings' >> /shared/bashrc
    echo export JOERN_SERVER_HOST={joern_host_escaped} >> /shared/bashrc
    echo export JOERN_SERVER_PORT={joern_port_escaped} >> /shared/bashrc
fi
"""

        msg, err = self.run_command_in_container(["bash", "-c", bash_script])
        if err != 0:
            logger.warning(f"Failed to write Joern env vars to /shared/bashrc: {msg}")
        else:
            logger.info(
                f"Joern environment variables written to /shared/bashrc: "
                f"JOERN_SERVER_HOST={joern_host}, JOERN_SERVER_PORT={joern_port}"
            )
