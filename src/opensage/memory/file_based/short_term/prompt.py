from __future__ import annotations

import os
import re

from opensage.memory.file_based.long_term import LONG_TERM_KNOWLEDGE_PATH

RUNTIME_MEMORY_CONTEXT_START = "[[OPENSAGE_RUNTIME_MEMORY_CONTEXT]]"
RUNTIME_MEMORY_CONTEXT_END = "[[/OPENSAGE_RUNTIME_MEMORY_CONTEXT]]"


def strip_runtime_memory_context(instruction: str) -> str:
    """Remove any previously injected runtime memory context block."""
    if not instruction:
        return ""
    pattern = (
        rf"\n\n{re.escape(RUNTIME_MEMORY_CONTEXT_START)}.*?"
        rf"{re.escape(RUNTIME_MEMORY_CONTEXT_END)}"
    )
    return re.sub(pattern, "", instruction, flags=re.DOTALL)


def build_file_runtime_memory_context(
    *, session_id: str, agent_name: str, agent_mem_dir: str
) -> str:
    """Build the file-based runtime memory prompt block."""
    guidance = (
        "- Your current memory mode is `file`.\n"
        f"- Your current `session_id` is `{session_id}`.\n"
        f"- Your current short-term memory directory is `{agent_mem_dir}`.\n"
        f"- Keep your working notes in `{os.path.join(agent_mem_dir, 'TODO.md')}`.\n"
        f"- Your full trajectory file is `{os.path.join(agent_mem_dir, 'traj.json')}`.\n"
        f"- Save long tool outputs under `{os.path.join(agent_mem_dir, 'tool_outputs')}/`.\n"
        f"- Shared long-term knowledge lives at `{LONG_TERM_KNOWLEDGE_PATH}`.\n"
    )
    return (
        f"{RUNTIME_MEMORY_CONTEXT_START}\n"
        "### Current Runtime Memory Context\n"
        f"- Your current agent is `{agent_name}`.\n"
        f"{guidance}"
        f"{RUNTIME_MEMORY_CONTEXT_END}"
    )


def inject_file_runtime_memory_context(
    *, instruction: str, session_id: str, agent_name: str, agent_mem_dir: str
) -> str:
    """Inject file-based runtime memory context into the instruction."""
    stripped_instruction = strip_runtime_memory_context(instruction)
    runtime_context = build_file_runtime_memory_context(
        session_id=session_id,
        agent_name=agent_name,
        agent_mem_dir=agent_mem_dir,
    )
    return f"{stripped_instruction}\n\n{runtime_context}"
