from __future__ import annotations

import os
import shlex

from opensage.memory.file_based.short_term.sandbox_io import (
    _get_main_sandbox,
    _write_text_to_main_sandbox,
)


def _long_term_root() -> str:
    from opensage.sandbox.sandbox_paths import get_mem_root

    return os.path.join(get_mem_root(), "long_term")


def _long_term_index_path() -> str:
    return os.path.join(_long_term_root(), "index.md")


def get_long_term_knowledge_path() -> str:
    return _long_term_index_path()


# Keep module-level name for any remaining imports that use it at call time.
LONG_TERM_KNOWLEDGE_PATH = "/mem/long_term/index.md"

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
    lt_root = _long_term_root()
    lt_index = _long_term_index_path()
    await sandbox.arun_command_in_container(f"mkdir -p {shlex.quote(lt_root)}")
    _, exit_code = await sandbox.arun_command_in_container(
        f"test -f {shlex.quote(lt_index)}"
    )
    if exit_code != 0:
        await _write_text_to_main_sandbox(invocation_context, lt_index, _INDEX_SEED)
