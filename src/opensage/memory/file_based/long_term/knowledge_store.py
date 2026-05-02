from __future__ import annotations

import os
import shlex

from opensage.memory.file_based.short_term.sandbox_io import (
    _get_main_sandbox,
    _write_text_to_main_sandbox,
)

MEM_ROOT_DIR = "/mem"
LONG_TERM_MEM_ROOT = os.path.join(MEM_ROOT_DIR, "long_term")
LONG_TERM_INDEX_PATH = os.path.join(LONG_TERM_MEM_ROOT, "index.md")

# Kept for backward import compatibility — the path now points at index.md, the
# top-level entry to long-term memory. Each piece of knowledge lives in its own
# .md file alongside index.md and is referenced from one line in index.md.
LONG_TERM_KNOWLEDGE_PATH = LONG_TERM_INDEX_PATH

_INDEX_SEED = (
    "# Long-term memory index\n"
    "\n"
    "Each line below references one knowledge file in this directory.\n"
    "Format: `filename.md — one-line summary`.\n"
    "\n"
)


async def ensure_long_term_knowledge_store(invocation_context) -> None:
    """Create the shared file-based long-term memory directory + seed index.md."""
    sandbox = _get_main_sandbox(invocation_context)
    await sandbox.arun_command_in_container(
        f"mkdir -p {shlex.quote(LONG_TERM_MEM_ROOT)}"
    )
    _, exit_code = await sandbox.arun_command_in_container(
        f"test -f {shlex.quote(LONG_TERM_INDEX_PATH)}"
    )
    if exit_code != 0:
        await _write_text_to_main_sandbox(
            invocation_context, LONG_TERM_INDEX_PATH, _INDEX_SEED
        )
