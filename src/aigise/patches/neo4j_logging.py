from __future__ import annotations

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

logger = logging.getLogger(__name__)

_enabled: bool = False
_patched: bool = False
_orig_agent_tool_run: Optional[Callable] = None
_orig_base_agent_run: Optional[Callable] = None


async def _record_agent_call(
    agent_tool: AgentTool,
    *,
    agent_tool_session_id: str,
    args,
    tool_context: ToolContext,
):
    # Lazy import to avoid circular imports during bootstrap
    from aigise.utils.neo4j_history_management import (  # type: ignore
        create_agent_call_relation,
    )

    """Create the agent call relationship before executing."""
    caller_agent_name = tool_context._invocation_context.agent.name
    callee_agent_name = agent_tool.agent.name
    caller_session_id = tool_context._invocation_context.session.id
    callee_session_id = agent_tool_session_id
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

    # Convert args to string for input_context
    input_content = args.get("request", "")
    output_content = "dummy"

    try:
        await create_agent_call_relation(
            caller_agent_name=caller_agent_name,
            callee_agent_name=callee_agent_name,
            caller_session_id=caller_session_id,
            callee_session_id=callee_session_id,
            input_content=input_content,
            output_content=output_content,
            caller_agent_model=caller_agent_model,
            callee_agent_model=callee_agent_model,
            context=tool_context,
        )
    except Exception as e:
        logger.error(f"Failed to create agent call relation: {e}")


async def _wrapped_base_agent_run(self, invocation_context):
    logging_enabled = _enabled
    if logging_enabled:
        from aigise.utils.neo4j_history_management import (  # type: ignore
            find_agent_run_by_session_id,
            log_single_event_neo4j,
            record_agent_end,
            record_agent_start,
            store_session_state,
        )

        await record_agent_start(self, invocation_context)

    session_id = invocation_context.session.id
    last_event = None
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
            yield event

    except Exception as e:
        if logging_enabled:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            if exc_traceback:
                traceback.print_tb(exc_traceback)
            logger.error(f"Failed to record agent run: {e}")
            await record_agent_end(invocation_context, "", "error")
        raise

    finally:
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
                await record_agent_end(invocation_context, output_content, "completed")
            except Exception as e:
                exc_type, exc_value, exc_traceback = sys.exc_info()
                if exc_traceback:
                    traceback.print_tb(exc_traceback)
                logger.error(f"Failed to record agent end: {e}")
                await record_agent_end(invocation_context, "", "error")

        # Write child's used llm calls into its session.state for parent to read
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


def apply() -> None:
    """Monkey-patch BaseAgent.run_async and AgentTool.run_async with toggle."""
    global _patched, _orig_agent_tool_run, _orig_base_agent_run
    if _patched:
        return

    _orig_agent_tool_run = AgentTool.run_async
    _orig_base_agent_run = BaseAgent.run_async

    async def _run_child_agent(
        agent_tool: AgentTool,
        args: dict[str, Any],
        tool_context: ToolContext,
        *,
        log_call: bool,
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
        from aigise.features.aigise_in_memory_session_service import (
            AigiseInMemorySessionService,
        )

        parent_plugins = []
        try:
            parent_plugins = list(
                tool_context._invocation_context.plugin_manager.plugins
            )
        except Exception as plugin_error:
            logger.debug("Failed to reuse parent plugins: %s", plugin_error)

        agentic_app = App(
            name=agent_tool.agent.name,
            root_agent=agent_tool.agent,
            plugins=parent_plugins,
        )

        runner = Runner(
            app=agentic_app,
            artifact_service=ForwardingArtifactService(tool_context),
            session_service=AigiseInMemorySessionService(),
            memory_service=InMemoryMemoryService(),
            credential_service=tool_context._invocation_context.credential_service,
        )
        session = await runner.session_service.create_session(
            app_name=agent_tool.agent.name,
            user_id="tmp_user",
            state=tool_context.state.to_dict(),
        )

        if log_call:
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
        remaining = min(50, max(0, (limit - used) if limit > 0 else 50))
        remaining_for_this_child = remaining

        last_event = None
        try:
            async for event in runner.run_async(
                user_id=session.user_id,
                session_id=session.id,
                new_message=content,
                run_config=RunConfig(max_llm_calls=remaining),
            ):
                try:
                    logger.warning(
                        f"[SUBAGENT:{agent_tool.agent.name}] {event.model_dump_json(exclude_none=True)}"
                    )
                except Exception as json_error:
                    # Handle Neo4j DateTime serialization error
                    logger.warning(
                        f"[SUBAGENT:{agent_tool.agent.name}] Event serialization failed: {json_error}, "
                        f"event_id={event.id}, event_type={getattr(event, 'type', 'unknown')}"
                    )
                if event.actions.state_delta:
                    tool_context.state.update(event.actions.state_delta)
                last_event = event
        except Exception as run_error:
            # Do not raise. Ask the model for a final summary using the session history.
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
            async for ev in runner.run_async(
                user_id=session.user_id,
                session_id=session.id,
                new_message=summary_content,
                run_config=RunConfig(max_llm_calls=min(remaining, 5)),
            ):
                fallback_last_event = ev
            if fallback_last_event:
                last_event = fallback_last_event
            # if child agent tool raises an error, we consider it has used all the remaining llm calls
            session.state["_adk"]["llm_calls_used"] = remaining_for_this_child

        return last_event, session

    async def _wrapped_agent_tool_run(
        self,
        *,
        args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Any:
        logger.warning(
            "[AgentToolPatch] invoked for agent=%s enabled=%s",
            getattr(self.agent, "name", "unknown"),
            _enabled,
        )
        last_event, child_session = await _run_child_agent(
            self, args, tool_context, log_call=_enabled
        )

        # Merge child's actually used llm calls back to parent for accurate countdown
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
                # If parent_ctx.increment_llm_call_count() exists, it should be the
                # authoritative way to account for usage (it may enforce limits).
                # Do not also add child_used again below, or we'll double count.
                logger.debug(
                    "Unable to increment parent LLM call count while merging child usage: %s",
                    limit_err,
                )

            if not incremented:
                # Fallback for contexts that don't expose increment_llm_call_count()
                # (e.g., some test stubs): adjust the counter directly once.
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


def enable() -> None:
    global _enabled
    _enabled = True


def disable() -> None:
    global _enabled
    _enabled = False


def is_enabled() -> bool:
    return _enabled
