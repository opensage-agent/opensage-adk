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
    """Build the file-based runtime memory prompt block.

    This block lists only **paths and locations** (the framework-level facts:
    where things live on disk). Policy / how-to-use guidance belongs in the
    user-configurable ``auto_insert_prompt_file`` and is injected as a
    separate block.
    """
    long_term_dir = os.path.dirname(LONG_TERM_KNOWLEDGE_PATH)
    facts = (
        f"- Agent: `{agent_name}` | session_id: `{session_id}`\n"
        f"- Short-term memory dir (per-agent): `{agent_mem_dir}/`\n"
        f"  - notes: `{os.path.join(agent_mem_dir, 'TODO.md')}`\n"
        f"  - pinned (compaction-safe): `{os.path.join(agent_mem_dir, 'pinned.md')}`\n"
        f"  - trajectory: `{os.path.join(agent_mem_dir, 'traj.json')}`\n"
        f"  - tool outputs: `{os.path.join(agent_mem_dir, 'tool_outputs')}/`\n"
        f"- Long-term memory dir (shared in this OpenSage session): `{long_term_dir}/`\n"
        f"  - index: `{os.path.join(long_term_dir, 'index.md')}`\n"
    )
    return (
        f"{RUNTIME_MEMORY_CONTEXT_START}\n"
        "### Current Runtime Memory Locations\n"
        f"{facts}"
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
