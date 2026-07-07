from __future__ import annotations

import logging
import os
import shlex

logger = logging.getLogger(__name__)

_WRITE_CHUNK_SIZE = 8192


def _get_main_sandbox(invocation_context):
    """Return main sandbox instance for current OpenSage session."""
    from opensage.session import get_opensage_session
    from opensage.utils.agent_utils import get_opensage_session_id_from_context

    opensage_session_id = get_opensage_session_id_from_context(invocation_context)
    opensage_session = get_opensage_session(opensage_session_id)
    return opensage_session.sandboxes.get_sandbox("main")


async def _write_text_to_main_sandbox(
    invocation_context, container_path: str, text: str
) -> None:
    """Write text into main sandbox as the container's default user."""
    sandbox = _get_main_sandbox(invocation_context)
    parent_dir = os.path.dirname(container_path)
    quoted_parent = shlex.quote(parent_dir or ".")
    quoted_path = shlex.quote(container_path)

    output, exit_code = await sandbox.arun_command_in_container(
        f"mkdir -p {quoted_parent} && rm -f {quoted_path} && : > {quoted_path}"
    )
    if exit_code != 0:
        raise RuntimeError(
            f"Failed to write sandbox file {container_path}: {output.strip()}"
        )

    for start in range(0, len(text), _WRITE_CHUNK_SIZE):
        chunk = text[start : start + _WRITE_CHUNK_SIZE]
        output, exit_code = await sandbox.arun_command_in_container(
            f"printf %s {shlex.quote(chunk)} >> {quoted_path}"
        )
        if exit_code != 0:
            raise RuntimeError(
                f"Failed to write sandbox file {container_path}: {output.strip()}"
            )

    output, exit_code = await sandbox.arun_command_in_container(
        f"chmod 0644 {quoted_path}"
    )
    if exit_code != 0:
        raise RuntimeError(
            f"Failed to write sandbox file {container_path}: {output.strip()}"
        )
