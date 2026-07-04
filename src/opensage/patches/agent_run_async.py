"""Patch: wrap ``BaseAgent.run_async`` to inject OpenSage file-memory setup.

At the start of every invocation we:

- Compute the agent's sandbox mem_dir and ensure its TODO.md / tool_outputs
  layout is created.
- Compute the host mem_dir and ensure it exists (for Web UI sub-agent
  visualization).
- Inject a runtime memory context block into ``agent.instruction`` (restored
  in the finally block).

During the run we also persist traj.json periodically to the host dir so the
Web UI can show live progress. OpenSageAgent rejects AgentTool entries during
agent construction.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from google.adk.agents.base_agent import BaseAgent

from opensage.memory.file_based.short_term.prompt import (
    build_file_runtime_memory_context,
    strip_runtime_memory_context,
)
from opensage.memory.file_based.short_term.session_files import (
    _compute_agent_mem_dir,
    _ensure_agent_mem_layout,
    _ensure_host_instance_dir,
    _get_host_instance_dir,
    _persist_traj_json,
    _persist_traj_json_to_host,
)
from opensage.utils.auto_insert_prompt import load_auto_insert_prompt_content

logger = logging.getLogger(__name__)

_patched: bool = False
_orig_base_agent_run: Optional[Callable] = None

AUTO_INSERT_PROMPT_START = "[[OPENSAGE_AUTO_INSERT_PROMPT]]"
AUTO_INSERT_PROMPT_END = "[[/OPENSAGE_AUTO_INSERT_PROMPT]]"


def _inject_runtime_blocks(
    agent: BaseAgent,
    *,
    session_id: str,
    agent_mem_dir: str,
    auto_insert_content: Optional[str],
) -> str | None:
    """Inject runtime blocks (memory context + auto-insert prompt) into
    ``agent.instruction``. Returns the original instruction so the caller can
    restore it after the invocation.
    """
    if not hasattr(agent, "instruction"):
        return None
    original_instruction = getattr(agent, "instruction", "") or ""
    # instruction may be a callable (bypasses ADK template substitution);
    # unwrap it to get the string for runtime block injection.
    if callable(original_instruction):
        _text = original_instruction(None)
    else:
        _text = original_instruction
    stripped_instruction = strip_runtime_memory_context(_text)
    runtime_context = build_file_runtime_memory_context(
        session_id=session_id,
        agent_name=getattr(agent, "name", "agent"),
        agent_mem_dir=agent_mem_dir,
    )
    parts = [stripped_instruction, runtime_context]
    if auto_insert_content and auto_insert_content.strip():
        parts.append(
            f"{AUTO_INSERT_PROMPT_START}\n"
            f"{auto_insert_content.rstrip()}\n"
            f"{AUTO_INSERT_PROMPT_END}"
        )
    final_text = "\n\n".join(parts)
    # Keep instruction as callable to bypass ADK template substitution.
    if callable(original_instruction):
        agent.instruction = lambda ctx, _t=final_text: _t
    else:
        agent.instruction = final_text
    return original_instruction


async def _wrapped_base_agent_run(self, invocation_context):
    session_id = invocation_context.session.id

    # sandbox mem_dir
    try:
        agent_mem_dir = _compute_agent_mem_dir(invocation_context)
    except Exception as mem_error:
        logger.warning("Failed to compute agent mem_dir: %s", mem_error)
        agent_mem_dir = None

    # host instance dir (for Web UI + orchestration persistence)
    host_dir = _get_host_instance_dir(invocation_context)
    if host_dir:
        try:
            _ensure_host_instance_dir(host_dir)
        except Exception:
            pass

    # Resolve user-configured auto-insert prompt content (defaults to a
    # template describing long-term memory policy).
    auto_insert_content: Optional[str] = None
    try:
        from opensage.session import get_opensage_session

        osid = invocation_context.session.state.get("opensage_session_id")
        if osid:
            opensage_session = get_opensage_session(osid, create_if_missing=False)
            if opensage_session is not None:
                auto_insert_content = load_auto_insert_prompt_content(opensage_session)
    except Exception:
        logger.exception("Failed to load auto_insert_prompt content")

    # inject runtime blocks into agent.instruction (restored in finally)
    original_instruction = None
    if agent_mem_dir:
        try:
            original_instruction = _inject_runtime_blocks(
                self,
                session_id=session_id,
                agent_mem_dir=agent_mem_dir,
                auto_insert_content=auto_insert_content,
            )
        except Exception as prompt_error:
            logger.warning("Failed to inject runtime prompt blocks: %s", prompt_error)

    # ensure TODO.md / tool_outputs layout exists in the sandbox
    if agent_mem_dir:
        try:
            await _ensure_agent_mem_layout(
                invocation_context,
                agent_mem_dir,
                agent_name=getattr(invocation_context.agent, "name", "agent"),
            )
        except Exception as mem_error:
            logger.warning("Failed to initialize agent memory dir: %s", mem_error)

    _host_event_count = 0
    try:
        async for event in _orig_base_agent_run(self, invocation_context):
            _host_event_count += 1
            # Incrementally persist traj.json to host dir for live Web UI view.
            if host_dir and _host_event_count % 3 == 0:
                try:
                    await _persist_traj_json_to_host(invocation_context, host_dir)
                except Exception:
                    pass
            yield event

    finally:
        if agent_mem_dir:
            try:
                await _persist_traj_json(invocation_context, agent_mem_dir)
            except Exception as mem_error:
                logger.warning("Failed to persist traj.json: %s", mem_error)

        if host_dir:
            try:
                await _persist_traj_json_to_host(invocation_context, host_dir)
            except Exception:
                pass

        # Track LLM call usage on the ADK session state for quota accounting.
        try:
            used_child = int(
                getattr(
                    getattr(invocation_context, "_invocation_cost_manager", None),
                    "_number_of_llm_calls",
                    0,
                )
                or 0
            )
            invocation_context.session.state.setdefault("_adk", {})
            invocation_context.session.state["_adk"]["llm_calls_used"] = used_child
        except Exception as _e:
            logger.debug(f"skip writing child llm_calls_used: {_e}")

        if original_instruction is not None:
            self.instruction = original_instruction


def apply() -> None:
    """Monkey-patch BaseAgent.run_async to inject OpenSage file-memory setup."""
    global _patched, _orig_base_agent_run
    if _patched:
        return
    _orig_base_agent_run = BaseAgent.run_async
    BaseAgent.run_async = _wrapped_base_agent_run
    _patched = True
