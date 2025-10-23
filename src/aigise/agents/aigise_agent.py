import logging
from typing import List, Optional

from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from pydantic import Field

from aigise.features.reward_logger import RewardLogger
from aigise.features.tool_combo import ToolCombo

logger = logging.getLogger(__name__)


class AigiseAgent(LlmAgent):
    tool_combos: Optional[List[ToolCombo]] = Field(default=None)
    reward_loggers: Optional[List[RewardLogger]] = Field(default=None)
    aigise_session_id: Optional[str] = Field(default=None)

    def __init__(
        self,
        *args,
        tools: Optional[List] = None,
        tool_combos: Optional[List[ToolCombo]] = None,
        reward_loggers: Optional[List[RewardLogger]] = None,
        aigise_session_id: Optional[str] = None,
        **kwargs,
    ):
        tools = list(tools) if tools else []

        sub_agents = kwargs.get("sub_agents", [])
        for combo in tool_combos or []:
            if combo.return_history:
                sub_agents.append(combo.sequential_agent)
            else:
                if combo.agent_tool not in tools:
                    tools.append(combo.agent_tool)

        kwargs["sub_agents"] = sub_agents
        kwargs["tools"] = tools

        # Initialize the parent class first
        super().__init__(*args, **kwargs)

        # Store the shared session id parameter
        self.aigise_session_id = aigise_session_id

        # Set up shared session id callback FIRST (highest priority)
        self._setup_aigise_callback()

        # Set up reward loggers after shared session id callback
        self.reward_loggers = reward_loggers or []
        self._setup_reward_loggers()

    def _setup_reward_loggers(self):
        """Set up reward loggers by registering appropriate callbacks."""
        if not self.reward_loggers:
            return

        # Collect existing callbacks
        existing_after_tool_callbacks = []
        if self.after_tool_callback:
            if isinstance(self.after_tool_callback, list):
                existing_after_tool_callbacks.extend(self.after_tool_callback)
            else:
                existing_after_tool_callbacks.append(self.after_tool_callback)

        existing_after_agent_callbacks = []
        if self.after_agent_callback:
            if isinstance(self.after_agent_callback, list):
                existing_after_agent_callbacks.extend(self.after_agent_callback)
            else:
                existing_after_agent_callbacks.append(self.after_agent_callback)

        # Add reward logger callbacks
        for reward_logger in self.reward_loggers:
            if reward_logger.tool_name:
                # Add tool callback
                tool_callback = reward_logger.create_after_tool_callback()
                existing_after_tool_callbacks = [
                    tool_callback
                ] + existing_after_tool_callbacks
            elif reward_logger.agent_name:
                # Add agent callback
                agent_callback = reward_logger.create_after_agent_callback()
                existing_after_agent_callbacks = [
                    agent_callback
                ] + existing_after_agent_callbacks

        # Update the callbacks
        if existing_after_tool_callbacks:
            self.after_tool_callback = existing_after_tool_callbacks

        if existing_after_agent_callbacks:
            self.after_agent_callback = existing_after_agent_callbacks

    def _setup_aigise_callback(self):
        """Set up callback to ensure aigise_session_id is stored and sandboxes are ready."""

        async def aigise_before_agent_callback(callback_context):
            session = callback_context._invocation_context.session

            # 1. Set aigise_session_id
            if "aigise_session_id" not in session.state:
                # Use the custom aigise_session_id if provided, otherwise use session.id
                session_id_to_use = (
                    self.aigise_session_id if self.aigise_session_id else session.id
                )
                session.state["aigise_session_id"] = session_id_to_use

            # 2. Collect sandbox dependencies and launch required sandboxes
            try:
                from aigise.session import get_aigise_session
                from aigise.toolbox.decorators import collect_sandbox_dependencies
                from aigise.utils.agent_utils import get_aigise_session_id_from_context

                # Get the root agent to collect all dependencies
                root_agent = self.root_agent

                # Collect all sandbox dependencies from the agent tree
                sandbox_dependencies = collect_sandbox_dependencies(root_agent)
                logger.info(f"Collected sandbox dependencies: {sandbox_dependencies}")
                if sandbox_dependencies:
                    logger.info(
                        f"Agent '{self.name}' requires sandboxes: {sandbox_dependencies}"
                    )

                    # Get AIgiSE session
                    aigise_session_id = get_aigise_session_id_from_context(
                        callback_context._invocation_context
                    )
                    aigise_session = get_aigise_session(aigise_session_id)

                    # Launch only the required sandboxes
                    # launch_all_sandboxes has defensive check, safe to call multiple times
                    await aigise_session.sandboxes.launch_all_sandboxes(
                        sandbox_types=sandbox_dependencies
                    )
                    logger.info(
                        f"Sandboxes launched successfully for agent '{self.name}'"
                    )
                else:
                    logger.debug(f"Agent '{self.name}' has no sandbox dependencies")
            except Exception as e:
                # Silent failure for non-AIgiSE scenarios or when no sandbox config
                logger.debug(f"Sandbox launch skipped for agent '{self.name}': {e}")

        if (
            not hasattr(self, "before_agent_callback")
            or self.before_agent_callback is None
        ):
            self.before_agent_callback = []
        elif not isinstance(self.before_agent_callback, list):
            self.before_agent_callback = [self.before_agent_callback]

        self.before_agent_callback = [
            aigise_before_agent_callback
        ] + self.before_agent_callback

        # Also set as attribute for Neo4j monkey patch to find and call early
        object.__setattr__(
            self,
            "aigise_before_agent_callback",
            aigise_before_agent_callback,
        )
