from __future__ import annotations

import sys
import traceback
from typing import Any, Callable, Optional

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.llm_agent import LlmAgent
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.tools._forwarding_artifact_service import ForwardingArtifactService
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from loguru import logger

from aigise.utils.neo4j_history_management import (
    create_agent_call_relation,
    find_agent_run_by_session_id,
    log_single_event_neo4j,
    record_agent_end,
    record_agent_start,
    store_session_state,
)


# This could only be enabled or disabled globally, for all agents in the same process
class Neo4jMonkeyPatchManager:
    def __init__(self):
        self._original_agent_tool_run: Optional[Callable] = None
        self._original_base_agent_run: Optional[Callable] = None
        self._patched = False

    def apply_patch(self):
        if self._patched:
            return

        self._original_agent_tool_run = AgentTool.run_async
        self._original_base_agent_run = BaseAgent.run_async

        original_base_agent_run = self._original_base_agent_run

        async def record_agent_call(
            agent_tool: AgentTool,
            *,
            agent_tool_session_id: str,
            args,
            tool_context: ToolContext,
        ):
            # Extract information for the agent relationship
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

            # Create the agent call relationship before executing
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

        async def enhanced_agent_tool_run(
            self,
            *,
            args: dict[str, Any],
            tool_context: ToolContext,
        ) -> Any:
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

            # modification starts here
            await record_agent_call(
                agent_tool=self,
                agent_tool_session_id=session.id,
                args=args,
                tool_context=tool_context,
            )
            # modification ends here

            last_event = None
            async for event in runner.run_async(
                user_id=session.user_id, session_id=session.id, new_message=content
            ):
                # Forward state delta to parent session.
                if event.actions.state_delta:
                    tool_context.state.update(event.actions.state_delta)
                last_event = event

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

        async def enhanced_base_agent_run(self, invocation_context):
            # Check for early callback to set aigise_session_id before record_agent_start
            if hasattr(self, "aigise_before_agent_callback") and callable(
                getattr(self, "aigise_before_agent_callback")
            ):
                # Create callback_context that matches what the function expects
                callback_context = CallbackContext(invocation_context)
                await self.aigise_before_agent_callback(callback_context)

            await record_agent_start(self, invocation_context)

            session_id = invocation_context.session.id
            last_event = None
            try:
                async for event in original_base_agent_run(self, invocation_context):
                    # Process each event immediately
                    try:
                        await log_single_event_neo4j(
                            event, session_id, invocation_context
                        )
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
                # Record agent end
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

        AgentTool.run_async = enhanced_agent_tool_run
        BaseAgent.run_async = enhanced_base_agent_run
        self._patched = True

    def remove_patch(self):
        if not self._patched:
            return

        if self._original_agent_tool_run:
            AgentTool.run_async = self._original_agent_tool_run

        if self._original_base_agent_run:
            BaseAgent.run_async = self._original_base_agent_run

        self._patched = False

    def is_patched(self) -> bool:
        """Check if the Neo4j patch is currently enabled.

        Returns:
            bool: True if the patch is enabled, False otherwise.
        """
        return self._patched

    def __enter__(self):
        self.apply_patch()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.remove_patch()


_patch_manager = None


def get_neo4j_patch_manager() -> Neo4jMonkeyPatchManager:
    global _patch_manager
    if _patch_manager is None:
        try:
            _patch_manager = Neo4jMonkeyPatchManager()
        except Exception as e:
            logger.error(f"Failed to create Neo4jMonkeyPatchManager: {e}")
            _patch_manager = None
    return _patch_manager


def enable_neo4j_logging():
    get_neo4j_patch_manager().apply_patch()


def disable_neo4j_logging():
    get_neo4j_patch_manager().remove_patch()


def is_neo4j_logging_enabled() -> bool:
    """Check if Neo4j logging patch is currently enabled.

    Returns:
        bool: True if Neo4j logging is enabled, False otherwise.
    """
    patch_manager = get_neo4j_patch_manager()
    if not patch_manager:
        return False
    return patch_manager.is_patched()
