import json
import os
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Union

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

# Type definitions for reward functions
RewardFunction = Callable[[Dict[str, Any], Optional[str]], float]


class RewardLogger:
    """
    A logger for recording rewards based on tool and agent executions.

    This class can be configured to log rewards for specific tools or agents.
    When a tool or agent is executed, the reward function calculates a reward value
    and logs it to a JSONL file.
    """

    def __init__(
        self,
        reward_function: RewardFunction,
        tool_name: Optional[str] = None,
        agent_name: Optional[str] = None,
        log_dir: str = ".logs",
    ):
        """
        Initialize the RewardLogger.

        Args:
            reward_function: A function that takes response_dict and message,
                           returns a float reward value
                           - For tools: Callable[[Dict[str, Any], Optional[str]], float] (tool_response dict)
                           - For agents: Callable[[Dict[str, Any], Optional[str]], float] ({"response": agent_text})
            tool_name: If provided, logs rewards for this specific tool
            agent_name: If provided, logs rewards for this specific agent
            log_dir: Directory to store log files
        """
        if tool_name is None and agent_name is None:
            raise ValueError("Either tool_name or agent_name must be provided")
        if tool_name is not None and agent_name is not None:
            raise ValueError("Only one of tool_name or agent_name should be provided")

        self.reward_function = reward_function
        self.tool_name = tool_name
        self.agent_name = agent_name
        self.log_dir = log_dir
        
        # Get reward function name automatically
        if hasattr(reward_function, '__name__'):
            self.reward_function_name = reward_function.__name__
        else:
            self.reward_function_name = str(reward_function)
        
        # Ensure log directory exists
        os.makedirs(log_dir, exist_ok=True)

    def _get_log_file_path(self, session_id: str) -> str:
        """Get the log file path for a given session."""
        return os.path.join(self.log_dir, session_id, "reward_logs.jsonl")

    def _ensure_session_log_dir(self, session_id: str):
        """Ensure the session-specific log directory exists."""
        session_log_dir = os.path.join(self.log_dir, session_id)
        os.makedirs(session_log_dir, exist_ok=True)

    def _log_reward(
        self,
        session_id: str,
        reward_type: str,
        reward_value: float,
        message: Optional[str] = None,
        additional_data: Optional[Dict[str, Any]] = None,
    ):
        """Log a reward entry to the JSONL file."""
        self._ensure_session_log_dir(session_id)
        log_file_path = self._get_log_file_path(session_id)

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "reward_type": reward_type,
            "reward_value": reward_value,
            "reward_function_name": self.reward_function_name,
            "message": message,
            **(additional_data or {}),
        }

        with open(log_file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    def _get_conversation_history(self, context) -> str:
        """Get the complete conversation history in ShareGPT JSON format."""
        try:
            # Both ToolContext and CallbackContext have _invocation_context.session
            session = context._invocation_context.session
            if not session or not session.events:
                return ""

            # Build conversation history in ShareGPT JSON format
            conversations = []
            for event in session.events:
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            # Regular text message
                            if event.author == "user":
                                conversations.append(
                                    {"from": "human", "value": part.text}
                                )
                            else:
                                conversations.append(
                                    {"from": "gpt", "value": part.text}
                                )
                        elif part.function_call:
                            # Function call as separate conversation item
                            function_call_data = {
                                "name": part.function_call.name,
                                "arguments": part.function_call.args or {},
                            }
                            conversations.append(
                                {
                                    "from": "function_call",
                                    "value": json.dumps(
                                        function_call_data, ensure_ascii=False
                                    ),
                                }
                            )
                        elif part.function_response:
                            # Function response as separate conversation item
                            conversations.append(
                                {
                                    "from": "observation",
                                    "value": json.dumps(
                                        part.function_response.response,
                                        ensure_ascii=False,
                                    ),
                                }
                            )

            # Get agent information
            agent = context._invocation_context.agent
            system_prompt = getattr(agent, "instruction", "")
            tools_info = []

            # Get tools information
            if hasattr(agent, "tools") and agent.tools:
                for tool in agent.tools:
                    # Handle different tool types
                    if hasattr(tool, "name"):
                        # Tool object
                        tool_info = {
                            "name": tool.name,
                            "description": getattr(tool, "description", ""),
                            "parameters": getattr(tool, "input_schema", {}),
                        }
                    elif hasattr(tool, "__name__"):
                        # Function object
                        tool_info = {
                            "name": tool.__name__,
                            "description": getattr(tool, "__doc__", ""),
                            "parameters": {},
                        }
                    else:
                        # Unknown tool type
                        tool_info = {
                            "name": str(tool),
                            "description": "",
                            "parameters": {},
                        }
                    tools_info.append(tool_info)

            # Create ShareGPT format
            sharegpt_format = [
                {
                    "conversations": conversations,
                    "system": system_prompt,
                    "tools": json.dumps(tools_info, ensure_ascii=False),
                }
            ]

            return json.dumps(sharegpt_format, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error getting conversation history: {e}")
            return ""

    def create_after_tool_callback(self):
        """Create an after_tool_callback function for tool reward logging."""
        if self.tool_name is None:
            raise ValueError("tool_name must be set to create after_tool_callback")

        def after_tool_callback(
            tool: BaseTool,
            args: Dict[str, Any],
            tool_context: ToolContext,
            tool_response: Dict[str, Any],
        ) -> Optional[Dict[str, Any]]:
            """After tool callback that logs rewards for specific tools."""
            if tool.name != self.tool_name:
                return None

            # Get the complete conversation history
            message = self._get_conversation_history(tool_context)

            # Calculate reward
            try:
                reward_value = self.reward_function(tool_response, message)

                # Get session_id from invocation context
                session_id = tool_context._invocation_context.session.id

                # Log the reward
                self._log_reward(
                    session_id=session_id,
                    reward_type="intermediate_reward",
                    reward_value=reward_value,
                    message=message,
                    additional_data={
                        "tool_name": tool.name,
                        "tool_args": args,
                        "tool_result": tool_response,
                    },
                )

            except Exception as e:
                print(f"Error calculating reward for tool {tool.name}: {e}")

            return None  # Don't modify the result

        return after_tool_callback

    def create_after_agent_callback(self):
        """Create an after_agent_callback function for agent reward logging."""
        if self.agent_name is None:
            raise ValueError("agent_name must be set to create after_agent_callback")

        def after_agent_callback(
            callback_context: CallbackContext,
        ) -> Optional[types.Content]:
            """After agent callback that logs rewards for specific agents."""
            if callback_context.agent_name != self.agent_name:
                return None  # Not the agent we're monitoring

            # Get the complete conversation history
            message = self._get_conversation_history(callback_context)

            # Extract agent's response content
            agent_response = ""
            try:
                # Get agent's response from session events
                session = callback_context._invocation_context.session
                if session and session.events:
                    # Get the most recent event from this agent (the one that just finished)
                    # We look for the last event with the matching agent name
                    current_agent_events = [
                        event
                        for event in session.events
                        if event.author == callback_context.agent_name
                    ]

                    if current_agent_events:
                        # Get the most recent event from this agent
                        latest_event = current_agent_events[-1]
                        if latest_event.content and latest_event.content.parts:
                            response_text = ""
                            for part in latest_event.content.parts:
                                if hasattr(part, "text") and part.text:
                                    response_text += part.text
                            if response_text:
                                agent_response = response_text
            except Exception as e:
                print(f"Error extracting agent response: {e}")
                agent_response = ""

            # Calculate reward using the agent's actual response
            try:
                reward_value = self.reward_function(
                    {"response": agent_response}, message
                )

                # Get session_id from invocation context
                session_id = callback_context._invocation_context.session.id

                # Log the reward
                self._log_reward(
                    session_id=session_id,
                    reward_type="final_reward",
                    reward_value=reward_value,
                    message=message,
                    additional_data={
                        "agent_name": callback_context.agent_name,
                        "invocation_id": callback_context.invocation_id,
                        "agent_response": agent_response,
                    },
                )

            except Exception as e:
                print(
                    f"Error calculating reward for agent {callback_context.agent_name}: {e}"
                )

            return None  # Don't modify the response

        return after_agent_callback
