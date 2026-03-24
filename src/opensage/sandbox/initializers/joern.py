"""Joern code analysis mixin."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import tempfile
import time

import networkx as nx

from opensage.sandbox.base_sandbox import BaseSandbox
from opensage.session.joern_client import JoernClient
from opensage.utils.merge_joern_codeql import (
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

    async def _async_initialize_impl(
        self: BaseSandbox, all_sandboxes: dict[str, BaseSandbox]
    ) -> bool:
        """Initialize Joern environment (async version)."""
        from opensage.session.opensage_session import get_opensage_session

        assert isinstance(self, BaseSandbox)

        logger.info(
            f"Async creating Joern environment for session {self.opensage_session_id}..."
        )

        opensage_session = get_opensage_session(self.opensage_session_id)
        assert "main" in all_sandboxes
        if not await all_sandboxes["main"].wait_for_ready_or_error():
            logger.error(f"Joern initialization failed: Main sandbox error")
            return False

        try:
            # Wrap Joern initialization with 10-minute timeout
            await asyncio.wait_for(
                self._initialize_joern_with_timeout(opensage_session, all_sandboxes),
                timeout=1200.0,  # 10 minutes
            )
        except asyncio.TimeoutError:
            logger.error(
                f"Joern initialization failed; timed out after 10 minutes for session {self.opensage_session_id}"
            )
            return False
        except Exception as e:
            logger.error(f"Joern initialization failed: {e}")
            return False
        return True

    async def _initialize_joern_with_timeout(
        self, opensage_session, all_sandboxes: dict[str, BaseSandbox]
    ) -> None:
        """Execute Joern initialization steps with timeout protection.

        Raises:
          RuntimeError: Raised when this operation fails."""

        t0 = time.monotonic()
        msg, err = self.run_command_in_container(
            ["bash", "/sandbox_scripts/callgraph/init.sh"],
            timeout=3600,
        )
        elapsed = time.monotonic() - t0
        logger.info(f"Joern init.sh completed in {elapsed:.1f}s (exit_code={err})")
        if err != 0:
            logger.error(f"Joern init failed (exit_code={err}), output:\n{msg}")
            raise RuntimeError(f"Joern init failed (exit_code={err})")

        t0 = time.monotonic()
        msg, err = self.run_command_in_container(
            [
                "bash",
                "/sandbox_scripts/callgraph/run_joern.sh",
                opensage_session.config.src_dir_in_sandbox,
            ],
            timeout=3600,
        )
        elapsed = time.monotonic() - t0
        logger.info(f"Joern run_joern.sh completed in {elapsed:.1f}s (exit_code={err})")
        if err != 0:
            logger.error(f"Joern run failed (exit_code={err}), output:\n{msg}")
            raise RuntimeError(f"Joern run failed (exit_code={err})")

        # wait for neo4j to be ready, such that we can import the CPG
        if not await all_sandboxes["neo4j"].wait_for_ready_or_error():
            raise RuntimeError(f"Joern initialization failed: Neo4j sandbox error")

        neo4j_client = await opensage_session.neo4j.get_async_client("analysis")

        await import_joern_callgraph(neo4j_client, "/")
        await update_joern_cpg(neo4j_client, fix_identical_methods=True)

    def _write_joern_env_to_bashrc(self, opensage_session) -> None:
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

    async def _ensure_ready_impl(self: BaseSandbox) -> bool:
        """Ensure Joern server is ready to be used."""

        from opensage.session.opensage_session import get_opensage_session

        try:
            assert isinstance(self, BaseSandbox)
            opensage_session = get_opensage_session(self.opensage_session_id)
            client = JoernClient(
                server_endpoint=f"{opensage_session.config.default_host}:18087"
            )

            await client.aexecute('importCpg("/cpg.bin")')
            # Write Joern server host to ~/.bashrc
            self._write_joern_env_to_bashrc(opensage_session)
        except Exception as e:
            logger.error(f"Joern server is collapsed during ensure_ready: {e}")
            return False
        return True
