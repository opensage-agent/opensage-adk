from __future__ import annotations

import logging
import sys
import traceback
from typing import Any, Callable, Optional

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.run_config import RunConfig
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
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


def apply() -> None:
    """Monkey-patch BaseAgent.run_async and AgentTool.run_async with toggle."""
    global _patched, _orig_agent_tool_run, _orig_base_agent_run
    if _patched:
        return

    _orig_agent_tool_run = AgentTool.run_async
    _orig_base_agent_run = BaseAgent.run_async

    async def _wrapped_agent_tool_run(
        self,
        *,
        args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Any:
        if not _enabled:
            return await _orig_agent_tool_run(
                self, args=args, tool_context=tool_context
            )

        # Preserve existing behavior when enabled
        if self.skip_summarization:
            tool_context.actions.skip_summarization = True

        if isinstance(self.agent, LlmAgent) and self.agent.input_schema:
            input_value = self.agent.input_schema.model_validate(args)
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
        runner = Runner(
            app_name=self.agent.name,
            agent=self.agent,
            artifact_service=ForwardingArtifactService(tool_context),
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
            credential_service=tool_context._invocation_context.credential_service,
        )
        session = await runner.session_service.create_session(
            app_name=self.agent.name,
            user_id="tmp_user",
            state=tool_context.state.to_dict(),
        )

        await _record_agent_call(
            agent_tool=self,
            agent_tool_session_id=session.id,
            args=args,
            tool_context=tool_context,
        )

        # Compute remaining quota from parent context and pass to child runner
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
        # Cap the max allowed LLM calls to 50
        remaining = min(50, max(0, (limit - used) if limit > 0 else 50))

        last_event = None
        try:
            async for event in runner.run_async(
                user_id=session.user_id,
                session_id=session.id,
                new_message=content,
                run_config=RunConfig(max_llm_calls=remaining),
            ):
                logger.warning(
                    f"[SUBAGENT:{self.agent.name}] {event.model_dump_json(exclude_none=True)}"
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

        # Merge child's actually used llm calls back to parent for accurate countdown
        try:
            child_session = await runner.session_service.get_session(
                app_name=self.agent.name, user_id=session.user_id, session_id=session.id
            )
            child_used = int(
                (
                    child_session.state.get("_adk", {}).get("llm_calls_used", 0)
                    if child_session and child_session.state
                    else 0
                )
                or 0
            )
            parent_mgr = getattr(parent_ctx, "_invocation_cost_manager", None)
            parent_used_now = int(getattr(parent_mgr, "_number_of_llm_calls", 0) or 0)
            if limit > 0:
                setattr(
                    parent_mgr,
                    "_number_of_llm_calls",
                    min(limit, parent_used_now + child_used),
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

    async def _wrapped_base_agent_run(self, invocation_context):
        if not _enabled:
            async for event in _orig_base_agent_run(self, invocation_context):
                yield event
            return
        # Lazy import to avoid circular imports during bootstrap
        from aigise.utils.neo4j_history_management import (  # type: ignore
            find_agent_run_by_session_id,
            log_single_event_neo4j,
            record_agent_end,
            record_agent_start,
            store_session_state,
        )

        # Allow early callback to set aigise_session_id before record_agent_start
        if hasattr(self, "aigise_before_agent_callback") and callable(
            getattr(self, "aigise_before_agent_callback")
        ):
            callback_context = CallbackContext(invocation_context)
            await self.aigise_before_agent_callback(callback_context)

        await record_agent_start(self, invocation_context)

        session_id = invocation_context.session.id
        last_event = None
        try:
            async for event in _orig_base_agent_run(self, invocation_context):
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
            exc_type, exc_value, exc_traceback = sys.exc_info()
            if exc_traceback:
                traceback.print_tb(exc_traceback)
            logger.error(f"Failed to record agent run: {e}")
            await record_agent_end(invocation_context, "", "error")
            raise

        # Store final session state after all events are processed
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
