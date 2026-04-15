from __future__ import annotations

import logging
import os
import shlex
import tempfile

logger = logging.getLogger(__name__)


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
    """Write text into main sandbox via temporary host file."""
    sandbox = _get_main_sandbox(invocation_context)
    parent_dir = os.path.dirname(container_path)
    await sandbox.arun_command_in_container(f"mkdir -p {shlex.quote(parent_dir)}")
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as temp_file:
        temp_file.write(text)
        local_path = temp_file.name
    try:
        await sandbox.acopy_file_to_container(local_path, container_path)
    finally:
        try:
            os.unlink(local_path)
        except OSError:
            logger.debug("Failed to cleanup temp file: %s", local_path)
