"""Neo4j Initializer."""

from __future__ import annotations

import asyncio
import logging
import shlex

from aigise.sandbox.base_sandbox import BaseSandbox
from aigise.sandbox.initializers.base import SandboxInitializer
from aigise.session.sandbox_state import SandboxState

logger = logging.getLogger(__name__)


class Neo4jInitializer(SandboxInitializer):
    """Initializer that initializes Neo4j code analysis capabilities to sandboxes."""

    async def ensure_ready(self) -> None:
        from aigise.session.aigise_session import get_aigise_session

        assert isinstance(self, BaseSandbox)

        msg, err = self.run_command_in_container(
            ["mkdir", "-p", "/shared/neo4j/import"]
        )
        if err != 0:
            raise RuntimeError(
                f"Neo4j initialization failed: import dir creation failed: {msg}"
            )

        logger.info(
            f"Async creating Neo4j environment for session {self.aigise_session_id}..."
        )
        aigise_session = get_aigise_session(self.aigise_session_id)
        aigise_session.sandboxes._sandboxes[self.sandbox_type] = self
        self.neo4j_client = aigise_session.neo4j.get_async_client_without_connection(
            "default"
        )
        while not await self.neo4j_client.verify_connection():
            await asyncio.sleep(10)

        # Write Neo4j connection info to /shared/bashrc for other containers to use
        self._write_neo4j_env_to_bashrc(aigise_session)

        aigise_session.sandboxes.set_sandbox_state(
            self.sandbox_type, SandboxState.READY
        )
        logger.info(
            f"Neo4j environment successfully initialized for session {self.aigise_session_id}"
        )

    def _write_neo4j_env_to_bashrc(self, aigise_session) -> None:
        """Write Neo4j connection environment variables to /shared/bashrc."""
        assert isinstance(self, BaseSandbox)

        # Check if Neo4j is configured
        if not aigise_session.config.neo4j:
            logger.info("Neo4j not configured, skipping environment variable setup")
            return

        # Get this container's IP address
        msg, err = self.run_command_in_container(["hostname", "-I"])
        if err != 0 or not msg.strip():
            logger.warning("Failed to get container IP, using fallback host")
            neo4j_host = "127.0.0.1"
        else:
            # hostname -I returns space-separated IPs, take the first one
            neo4j_host = msg.strip().split()[0]

        neo4j_port = aigise_session.config.neo4j.bolt_port
        neo4j_user = aigise_session.config.neo4j.user or "neo4j"
        neo4j_password = aigise_session.config.neo4j.password or "callgraphn4j!"

        # Escape values for safe use in bash script
        neo4j_host_escaped = shlex.quote(neo4j_host)
        neo4j_port_escaped = shlex.quote(str(neo4j_port))
        neo4j_user_escaped = shlex.quote(neo4j_user)
        neo4j_password_escaped = shlex.quote(neo4j_password)

        # Create bash script to write to /shared/bashrc (avoid duplicates)
        bash_script = f"""
# Ensure /shared directory exists
mkdir -p /shared

# Check if Neo4j env vars already exist
if ! grep -q "export NEO4J_HOST=" /shared/bashrc 2>/dev/null; then
    echo '' >> /shared/bashrc
    echo '# Neo4j connection settings' >> /shared/bashrc
    echo export NEO4J_HOST={neo4j_host_escaped} >> /shared/bashrc
    echo export NEO4J_PORT={neo4j_port_escaped} >> /shared/bashrc
    echo export NEO4J_USER={neo4j_user_escaped} >> /shared/bashrc
    echo export NEO4J_PASSWORD={neo4j_password_escaped} >> /shared/bashrc
fi
"""

        msg, err = self.run_command_in_container(["bash", "-c", bash_script])
        if err != 0:
            logger.warning(f"Failed to write Neo4j env vars to /shared/bashrc: {msg}")
        else:
            logger.info(
                f"Neo4j environment variables written to /shared/bashrc: "
                f"NEO4J_HOST={neo4j_host}, NEO4J_PORT={neo4j_port}"
            )
