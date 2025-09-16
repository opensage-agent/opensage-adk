from typing import List, Optional

from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from pydantic import Field

from aigise.extended_features.reward_logger import RewardLogger
from aigise.extended_features.tool_combo import ToolCombo


class SecAgent(LlmAgent):
    tool_combos: Optional[List[ToolCombo]] = Field(default=None)
    reward_loggers: Optional[List[RewardLogger]] = Field(default=None)

    def __init__(
        self,
        *args,
        tools: Optional[List] = None,
        tool_combos: Optional[List[ToolCombo]] = None,
        reward_loggers: Optional[List[RewardLogger]] = None,
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
        if sub_agents:
            sub_agents_str = "\n".join(
                [f"{agent.name}: {agent.description}" for agent in sub_agents]
            )
            kwargs["instruction"] += (
                f"\n\nYou have the following sub-agents: \n{sub_agents_str}"
            )
            kwargs["instruction"] += (
                f"\n\nDelegate the task to the sub-agents if necessary."
            )
        kwargs["tools"] = tools

        # Initialize the parent class first
        super().__init__(*args, **kwargs)

        # Set up reward loggers after parent initialization
        self.reward_loggers = reward_loggers or []
        self._setup_reward_loggers()

        # Set up shared session id callback
        self._setup_shared_session_id_callback()

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
                existing_after_tool_callbacks.append(tool_callback)
            elif reward_logger.agent_name:
                # Add agent callback
                agent_callback = reward_logger.create_after_agent_callback()
                existing_after_agent_callbacks.append(agent_callback)

        # Update the callbacks
        if existing_after_tool_callbacks:
            self.after_tool_callback = existing_after_tool_callbacks

        if existing_after_agent_callbacks:
            self.after_agent_callback = existing_after_agent_callbacks

    def _setup_shared_session_id_callback(self):
        """Set up callback to ensure shared_session_id is stored in session state."""

        def before_agent_callback(callback_context):
            session = callback_context._invocation_context.session
            if "shared_session_id" not in session.state:
                session.state["shared_session_id"] = session.id

        if (
            not hasattr(self, "before_agent_callback")
            or self.before_agent_callback is None
        ):
            self.before_agent_callback = []
        elif not isinstance(self.before_agent_callback, list):
            self.before_agent_callback = [self.before_agent_callback]

        self.before_agent_callback.append(before_agent_callback)
