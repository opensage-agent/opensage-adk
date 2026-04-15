from __future__ import annotations

import os
import shlex

from opensage.memory.file_based.short_term.sandbox_io import (
    _get_main_sandbox,
    _write_text_to_main_sandbox,
)

MEM_ROOT_DIR = "/mem"
LONG_TERM_MEM_ROOT = os.path.join(MEM_ROOT_DIR, "long_term")
LONG_TERM_KNOWLEDGE_DIR = os.path.join(LONG_TERM_MEM_ROOT, "knowledge")
LONG_TERM_KNOWLEDGE_PATH = os.path.join(LONG_TERM_KNOWLEDGE_DIR, "knowledge.json")


async def ensure_long_term_knowledge_store(invocation_context) -> None:
    """Create the shared file-based long-term knowledge store if missing."""
    sandbox = _get_main_sandbox(invocation_context)
    await sandbox.arun_command_in_container(
        f"mkdir -p {shlex.quote(LONG_TERM_KNOWLEDGE_DIR)}"
    )
    _, exit_code = await sandbox.arun_command_in_container(
        f"test -f {shlex.quote(LONG_TERM_KNOWLEDGE_PATH)}"
    )
    if exit_code != 0:
        await _write_text_to_main_sandbox(
            invocation_context, LONG_TERM_KNOWLEDGE_PATH, "{}\n"
        )
