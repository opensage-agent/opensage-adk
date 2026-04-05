from __future__ import annotations

import copy
import logging
import sys
import traceback
from typing import Any, Callable, Optional

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.run_config import RunConfig
from google.adk.apps.app import App
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.tools._forwarding_artifact_service import ForwardingArtifactService
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from opensage.memory.database_based.short_term.config import (
    is_database_short_term_enabled_from_context,
)
from opensage.memory.file_based.short_term.prompt import (
    RUNTIME_MEMORY_CONTEXT_END,
    RUNTIME_MEMORY_CONTEXT_START,
    build_file_runtime_memory_context,
    strip_runtime_memory_context,
)
from opensage.memory.file_based.short_term.session_files import (
    HOST_MEM_DIR_KEY,
    MEM_AGENT_DIR_KEY,
    _compute_agent_mem_dir,
    _compute_child_session_mem_dir,
    _compute_host_child_mem_dir,
    _ensure_agent_mem_layout,
    _ensure_host_mem_dir,
    _get_host_mem_dir,
    _persist_traj_json,
    _persist_traj_json_to_host,
)

logger = logging.getLogger(__name__)

_patched: bool = False
_orig_base_agent_run: Optional[Callable] = None
_MEMORY_MANAGEMENT_FILE = "file"
_MEMORY_MANAGEMENT_DATABASE = "database"


def _normalize_memory_management(memory_management: Any) -> str:
    """Normalize configured memory management mode."""
    value = getattr(memory_management, "value", memory_management)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in (_MEMORY_MANAGEMENT_FILE, _MEMORY_MANAGEMENT_DATABASE):
            return normalized
    return _MEMORY_MANAGEMENT_FILE


def _get_memory_management_from_opensage_session_id(opensage_session_id: str) -> str:
    """Read memory management mode from the OpenSage session config."""
    from opensage.session import get_opensage_session

    opensage_session = get_opensage_session(opensage_session_id)
    memory_config = getattr(getattr(opensage_session, "config", None), "memory", None)
    return _normalize_memory_management(getattr(memory_config, "management", None))


def _get_memory_management_from_context(context) -> str:
    """Read memory management mode from the current invocation/tool context."""
    from opensage.utils.agent_utils import get_opensage_session_id_from_context

    opensage_session_id = get_opensage_session_id_from_context(context)
    return _get_memory_management_from_opensage_session_id(opensage_session_id)


def _is_file_memory_enabled_for_context(context) -> bool:
    """Return whether current context is configured to use file memory."""
    return _get_memory_management_from_context(context) == _MEMORY_MANAGEMENT_FILE


def _is_database_memory_enabled_for_context(context) -> bool:
    """Return whether current context is configured to use database memory."""
    return _get_memory_management_from_context(context) == _MEMORY_MANAGEMENT_DATABASE


def _build_runtime_memory_context(
    *,
    memory_management: str,
    session_id: str,
    agent_name: str,
    agent_mem_dir: str | None = None,
) -> str:
    """Build the session-specific runtime memory prompt block."""
    if memory_management == _MEMORY_MANAGEMENT_DATABASE:
        guidance = (
            "- Your current memory mode is `database`.\n"
            f"- Your current `session_id` is `{session_id}`.\n"
            "- Use the `memory_management_agent` tool for memory retrieval and memory-aware history inspection.\n"
            "- Do not assume file-based memory directories exist for this run.\n"
        )
    else:
        if not agent_mem_dir:
            raise ValueError("agent_mem_dir is required for file memory context")
        return build_file_runtime_memory_context(
            session_id=session_id,
            agent_name=agent_name,
            agent_mem_dir=agent_mem_dir,
        )
    return (
        f"{RUNTIME_MEMORY_CONTEXT_START}\n"
        "### Current Runtime Memory Context\n"
        f"- Your current agent is `{agent_name}`.\n"
        f"{guidance}"
        f"{RUNTIME_MEMORY_CONTEXT_END}"
    )


def _inject_runtime_memory_context(
    agent: BaseAgent,
    *,
    memory_management: str,
    session_id: str,
    agent_mem_dir: str | None = None,
) -> str | None:
    """Inject session-specific runtime memory context into the agent instruction."""
    if not hasattr(agent, "instruction"):
        return None

    original_instruction = getattr(agent, "instruction", "") or ""
    stripped_instruction = strip_runtime_memory_context(original_instruction)
    runtime_context = _build_runtime_memory_context(
        memory_management=memory_management,
        session_id=session_id,
        agent_name=getattr(agent, "name", "agent"),
        agent_mem_dir=agent_mem_dir,
    )
    agent.instruction = f"{stripped_instruction}\n\n{runtime_context}"
    return original_instruction


def _extract_tool_name(tool: Any) -> str:
    """Best-effort tool name extraction for runtime tool injection."""
    name = getattr(tool, "name", None)
    if isinstance(name, str) and name:
        return name
    nested_agent = getattr(tool, "agent", None)
    nested_agent_name = getattr(nested_agent, "name", None)
    if isinstance(nested_agent_name, str) and nested_agent_name:
        return nested_agent_name
    name = getattr(tool, "__name__", None)
    return name if isinstance(name, str) else ""


def _inject_runtime_memory_tools(
    agent: BaseAgent, invocation_context
) -> list[Any] | None:
    """Temporarily inject memory tools for the current runtime if needed."""
    if not _is_database_memory_enabled_for_context(invocation_context):
        return None
    if getattr(agent, "name", None) == "memory_management_agent":
        return None
    tools = getattr(agent, "tools", None)
    if tools is None:
        return None
    if any(_extract_tool_name(tool) == "memory_management_agent" for tool in tools):
        return None

    from opensage.util_agents.memory_management_agent.agent import (
        create_memory_management_agent_tool,
    )
    from opensage.utils.agent_utils import get_model_from_agent

    model = get_model_from_agent(agent)
    if model is None:
        logger.warning(
            "Failed to inject database memory tool for agent %s: model unavailable",
            getattr(agent, "name", "agent"),
        )
        return None

    original_tools = list(tools)
    memory_tool = create_memory_management_agent_tool(model=model)
    agent.tools = original_tools + [memory_tool]
    return original_tools


def _clone_agent_for_child_session(agent: BaseAgent) -> BaseAgent:
    """Shallow copy the child agent before the child invocation starts."""
    try:
        cloned_agent = agent.model_copy(deep=False)
    except Exception:
        cloned_agent = copy.copy(agent)
    return cloned_agent


def _extract_query_from_invocation_context(invocation_context) -> str:
    """Best-effort query extraction from current invocation."""
    try:
        user_content = getattr(invocation_context, "user_content", None)
        if user_content and getattr(user_content, "parts", None):
            for part in reversed(user_content.parts):
                text = getattr(part, "text", "")
                if isinstance(text, str) and text:
                    return text
    except Exception:
        logger.debug("Failed to extract query from invocation_context.user_content")
    return ""


async def _record_agent_call(
    agent_tool: AgentTool,
    *,
    agent_tool_session_id: str,
    args,
    tool_context: ToolContext,
):
    """Create the agent call relationship before executing."""
    logging_enabled = is_database_short_term_enabled_from_context(tool_context)
    caller_agent_name = tool_context._invocation_context.agent.name
    callee_agent_name = agent_tool.agent.name
    caller_session_id = tool_context._invocation_context.session.id
    callee_session_id = agent_tool_session_id
    input_content = args.get("request", "")
    if logging_enabled:
        from opensage.memory.database_based.short_term.history_store import (  # type: ignore
            create_agent_call_relation,
        )

        caller_agent_model = (
            tool_context._invocation_context.agent.model
            if hasattr(tool_context._invocation_context.agent, "model")
            and isinstance(tool_context._invocation_context.agent.model, str)
            else tool_context._invocation_context.agent.model.model
            if hasattr(tool_context._invocation_context.agent, "model")
            else "No model"
        )
        callee_agent_model = (
            agent_tool.agent.model
            if hasattr(agent_tool.agent, "model")
            and isinstance(agent_tool.agent.model, str)
            else agent_tool.agent.model.model
            if hasattr(agent_tool.agent, "model")
            else "No model"
        )
        try:
            await create_agent_call_relation(
                caller_agent_name=caller_agent_name,
                callee_agent_name=callee_agent_name,
                caller_session_id=caller_session_id,
                callee_session_id=callee_session_id,
                input_content=input_content,
                output_content="",
                caller_agent_model=caller_agent_model,
                callee_agent_model=callee_agent_model,
                context=tool_context,
            )
        except Exception as e:
            logger.error(f"Failed to create agent call relation: {e}")


async def _wrapped_base_agent_run(self, invocation_context):
    logging_enabled = is_database_short_term_enabled_from_context(invocation_context)
    session_id = invocation_context.session.id
    memory_management = _get_memory_management_from_context(invocation_context)
    file_memory_enabled = memory_management == _MEMORY_MANAGEMENT_FILE
    agent_mem_dir = None
    if file_memory_enabled:
        agent_mem_dir = _compute_agent_mem_dir(invocation_context)
    # Host dir: always create (for Web UI sub-agent visualization)
    host_dir = _get_host_mem_dir(invocation_context)
    if host_dir:
        try:
            _ensure_host_mem_dir(host_dir)
        except Exception:
            pass
    original_instruction = None
    original_tools = None
    try:
        original_tools = _inject_runtime_memory_tools(self, invocation_context)
    except Exception as tool_error:
        logger.warning("Failed to inject runtime memory tools: %s", tool_error)
    if memory_management in (
        _MEMORY_MANAGEMENT_FILE,
        _MEMORY_MANAGEMENT_DATABASE,
    ):
        try:
            original_instruction = _inject_runtime_memory_context(
                self,
                memory_management=memory_management,
                session_id=session_id,
                agent_mem_dir=agent_mem_dir,
            )
        except Exception as prompt_error:
            logger.warning(
                "Failed to inject runtime memory prompt context: %s", prompt_error
            )
    if file_memory_enabled:
        try:
            _ensure_agent_mem_layout(
                invocation_context,
                agent_mem_dir,
                agent_name=getattr(invocation_context.agent, "name", "agent"),
            )
        except Exception as mem_error:
            logger.warning("Failed to initialize agent memory dir: %s", mem_error)
    if logging_enabled:
        from opensage.memory.database_based.short_term.history_store import (  # type: ignore
            find_agent_run_by_session_id,
            log_single_event_neo4j,
            record_agent_end,
            record_agent_start,
            store_session_state,
        )

        await record_agent_start(self, invocation_context)

    last_event = None
    run_failed = False
    _host_event_count = 0
    try:
        async for event in _orig_base_agent_run(self, invocation_context):
            if logging_enabled:
                try:
                    await log_single_event_neo4j(event, session_id, invocation_context)
                except Exception as event_error:
                    exc_type, exc_value, exc_traceback = sys.exc_info()
                    if exc_traceback:
                        traceback.print_tb(exc_traceback)
                    logger.error(f"Failed to process event: {event_error}")
                    raise

            last_event = event
            # Incrementally persist traj to host for live sub-agent viewing
            _host_event_count += 1
            if host_dir and _host_event_count % 3 == 0:
                try:
                    _persist_traj_json_to_host(invocation_context, host_dir)
                except Exception:
                    pass
            yield event

    except Exception as e:
        run_failed = True
        if logging_enabled:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            if exc_traceback:
                traceback.print_tb(exc_traceback)
            logger.error(f"Failed to record agent run: {e}")
            await record_agent_end(invocation_context, "", "error")
        raise

    finally:
        if file_memory_enabled:
            try:
                _persist_traj_json(invocation_context, agent_mem_dir)
            except Exception as mem_error:
                logger.warning("Failed to persist traj.json: %s", mem_error)

        # Host traj (always, for Web UI)
        if host_dir:
            try:
                _persist_traj_json_to_host(invocation_context, host_dir)
            except Exception:
                pass

        if logging_enabled:
            try:
                final_session_state = invocation_context.session.state
                found_session = await find_agent_run_by_session_id(
                    session_id, invocation_context
                )
                if found_session:
                    await store_session_state(
                        session_id, final_session_state, invocation_context
                    )
            except Exception as state_error:
                exc_type, exc_value, exc_traceback = sys.exc_info()
                if exc_traceback:
                    traceback.print_tb(exc_traceback)
                logger.error(f"Failed to store final session state: {state_error}")

            try:
                output_content = ""
                if last_event and last_event.content and last_event.content.parts:
                    output_content = "\n".join(
                        p.text
                        for p in last_event.content.parts
                        if hasattr(p, "text") and p.text
                    )
                if not run_failed:
                    await record_agent_end(
                        invocation_context, output_content, "completed"
                    )
            except Exception as e:
                exc_type, exc_value, exc_traceback = sys.exc_info()
                if exc_traceback:
                    traceback.print_tb(exc_traceback)
                logger.error(f"Failed to record agent end: {e}")
                await record_agent_end(invocation_context, "", "error")

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
        if original_tools is not None:
            self.tools = original_tools


def apply() -> None:
    """Monkey-patch BaseAgent.run_async and AgentTool.run_async with toggle."""
    global _patched, _orig_base_agent_run
    if _patched:
        return

    _orig_base_agent_run = BaseAgent.run_async

    async def _run_child_agent(
        agent_tool: AgentTool,
        args: dict[str, Any],
        tool_context: ToolContext,
    ) -> tuple[Any, Any]:
        """Execute the wrapped agent and return (last_event, child_session)."""
        if agent_tool.skip_summarization:
            tool_context.actions.skip_summarization = True

        if isinstance(agent_tool.agent, LlmAgent) and agent_tool.agent.input_schema:
            input_value = agent_tool.agent.input_schema.model_validate(args)
            content = types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=input_value.model_dump_json(exclude_none=True)
                    )
                ],
            )
        else:
            content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=args["request"])],
            )
        from opensage.features.opensage_in_memory_session_service import (
            OpenSageInMemorySessionService,
        )

        session_service = OpenSageInMemorySessionService()
        session_state = tool_context.state.to_dict()
        from opensage.utils.agent_utils import get_opensage_session_id_from_context

        session_state["opensage_session_id"] = get_opensage_session_id_from_context(
            tool_context
        )
        session = await session_service.create_session(
            app_name=agent_tool.agent.name,
            user_id="tmp_user",
            state=session_state,
        )
        if _is_file_memory_enabled_for_context(tool_context):
            child_agent_mem_dir = _compute_child_session_mem_dir(
                tool_context._invocation_context,
                child_agent_name=agent_tool.agent.name,
                child_session_id=session.id,
            )
            session.state[MEM_AGENT_DIR_KEY] = child_agent_mem_dir
            try:
                _ensure_agent_mem_layout(
                    tool_context._invocation_context,
                    child_agent_mem_dir,
                    agent_name=agent_tool.agent.name,
                )
            except Exception as mem_error:
                logger.warning(
                    "Failed to initialize child agent memory dir: %s", mem_error
                )

        # Host path: always set (for Web UI, regardless of memory mode)
        parent_host_dir = tool_context._invocation_context.session.state.get(
            HOST_MEM_DIR_KEY
        )
        if parent_host_dir:
            child_host_dir = _compute_host_child_mem_dir(
                parent_host_dir,
                child_agent_name=agent_tool.agent.name,
                child_session_id=session.id,
            )
            session.state[HOST_MEM_DIR_KEY] = child_host_dir
            try:
                _ensure_host_mem_dir(child_host_dir)
            except Exception:
                pass

        parent_plugins = []
        try:
            parent_plugins = list(
                tool_context._invocation_context.plugin_manager.plugins
            )
        except Exception as plugin_error:
            logger.debug("Failed to reuse parent plugins: %s", plugin_error)

        child_agent = _clone_agent_for_child_session(agent_tool.agent)
        agentic_app = App(
            name=child_agent.name,
            root_agent=child_agent,
            plugins=parent_plugins,
        )

        runner = Runner(
            app=agentic_app,
            artifact_service=ForwardingArtifactService(tool_context),
            session_service=session_service,
            memory_service=InMemoryMemoryService(),
            credential_service=tool_context._invocation_context.credential_service,
        )

        await _record_agent_call(
            agent_tool=agent_tool,
            agent_tool_session_id=session.id,
            args=args,
            tool_context=tool_context,
        )

        parent_ctx = tool_context._invocation_context
        limit = int(
            getattr(getattr(parent_ctx, "run_config", None), "max_llm_calls", 0) or 0
        )
        used = int(
            getattr(
                getattr(parent_ctx, "_invocation_cost_manager", None),
                "_number_of_llm_calls",
                0,
            )
            or 0
        )
        # Read per-subagent cap from config (0 = unlimited)
        subagent_cap = 0
        try:
            from opensage.session import get_opensage_session
            from opensage.utils.agent_utils import get_opensage_session_id_from_context

            _osid = get_opensage_session_id_from_context(tool_context)
            _os_session = get_opensage_session(_osid)
            _subagent_cfg = getattr(_os_session.config, "subagent", None)
            if _subagent_cfg:
                subagent_cap = int(getattr(_subagent_cfg, "max_llm_calls", 0) or 0)
        except Exception:
            pass
        parent_remaining = max(0, limit - used) if limit > 0 else None
        if subagent_cap > 0 and parent_remaining is not None:
            remaining = min(subagent_cap, parent_remaining)
        elif subagent_cap > 0:
            remaining = subagent_cap
        elif parent_remaining is not None:
            remaining = parent_remaining
        else:
            remaining = None  # no cap — use ADK default
        remaining_for_this_child = remaining

        run_cfg = (
            RunConfig(max_llm_calls=remaining) if remaining is not None else RunConfig()
        )

        last_event = None
        try:
            async for event in runner.run_async(
                user_id=session.user_id,
                session_id=session.id,
                new_message=content,
                run_config=run_cfg,
            ):
                try:
                    logger.warning(
                        f"[SUBAGENT:{agent_tool.agent.name}] {event.model_dump_json(exclude_none=True)}"
                    )
                except Exception as json_error:
                    logger.warning(
                        f"[SUBAGENT:{agent_tool.agent.name}] Event serialization failed: {json_error}, "
                        f"event_id={event.id}, event_type={getattr(event, 'type', 'unknown')}"
                    )
                if event.actions.state_delta:
                    tool_context.state.update(event.actions.state_delta)
                last_event = event
        except Exception as run_error:
            logger.error(f"Subagent run error, switching to final summary: {run_error}")
            summary_prompt = (
                "Final summary requested due to internal error.\n\n"
                "Instructions:\n"
                "1) Provide a concise final summary based ONLY on the existing history.\n"
                "2) Include sections: Summary, What was verified, Next steps, Known blockers.\n"
                "3) Do not speculate beyond the evidence.\n\n"
                f"We just encountered an error: {repr(run_error)}\n"
            )
            summary_content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=summary_prompt)],
            )
            fallback_last_event = None
            fallback_cap = min(remaining, 5) if remaining is not None else 5
            async for ev in runner.run_async(
                user_id=session.user_id,
                session_id=session.id,
                new_message=summary_content,
                run_config=RunConfig(max_llm_calls=fallback_cap),
            ):
                fallback_last_event = ev
            if fallback_last_event:
                last_event = fallback_last_event
            session.state["_adk"]["llm_calls_used"] = remaining_for_this_child or 0

        return last_event, session

    async def _wrapped_agent_tool_run(
        self,
        *,
        args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Any:
        logger.warning(
            "[AgentToolPatch] invoked for agent=%s short_term_db=%s",
            getattr(self.agent, "name", "unknown"),
            is_database_short_term_enabled_from_context(tool_context),
        )
        last_event, child_session = await _run_child_agent(self, args, tool_context)

        try:
            parent_ctx = tool_context._invocation_context
            parent_mgr = getattr(parent_ctx, "_invocation_cost_manager", None)
            parent_limit = int(
                getattr(getattr(parent_ctx, "run_config", None), "max_llm_calls", 0)
                or 0
            )
            child_used = int(
                (
                    child_session
                    and child_session.state
                    and child_session.state.get("_adk", {}).get("llm_calls_used", 0)
                )
                or 0
            )
            incremented = False
            try:
                for _ in range(child_used):
                    parent_ctx.increment_llm_call_count()
                incremented = True
            except Exception as limit_err:
                logger.debug(
                    "Unable to increment parent LLM call count while merging child usage: %s",
                    limit_err,
                )

            if not incremented:
                parent_used_now = int(
                    getattr(parent_mgr, "_number_of_llm_calls", 0) or 0
                )
                if parent_limit > 0:
                    setattr(
                        parent_mgr,
                        "_number_of_llm_calls",
                        min(parent_limit, parent_used_now + child_used),
                    )
                else:
                    setattr(
                        parent_mgr, "_number_of_llm_calls", parent_used_now + child_used
                    )
        except Exception as _e:
            logger.debug(f"skip merging child llm_calls_used: {_e}")

        if not last_event or not last_event.content or not last_event.content.parts:
            return ""
        merged_text = "\n".join(p.text for p in last_event.content.parts if p.text)
        if isinstance(self.agent, LlmAgent) and self.agent.output_schema:
            tool_result = self.agent.output_schema.model_validate_json(
                merged_text
            ).model_dump(exclude_none=True)
        else:
            tool_result = merged_text
        return tool_result

    AgentTool.run_async = _wrapped_agent_tool_run
    BaseAgent.run_async = _wrapped_base_agent_run
    _patched = True
