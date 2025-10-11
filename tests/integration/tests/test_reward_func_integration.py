"""
Test RewardLogger functionality with mathematical operations.

This test verifies that:
1. Agent correctly calculates mathematical operations
2. RewardLogger logs rewards to files
3. Temporary log files are cleaned up after tests
"""

import importlib
import json
import os
import re
import shutil
import warnings
from pathlib import Path
from typing import List

import pytest
from google.adk import Runner
from google.adk.events import Event
from google.adk.sessions import InMemorySessionService
from google.genai import types
from loguru import logger

# Filter out Pydantic serialization warnings from LiteLLM
warnings.filterwarnings("ignore", message=".*Pydantic serializer warnings.*")
warnings.filterwarnings("ignore", message=".*PydanticSerializationUnexpectedValue.*")


class RewardFuncTestRunner:
    """Test runner for RewardLogger tests."""

    app_name = "reward_func_test"
    user_id = "test_user"

    def __init__(self, agent):
        self.agent = agent
        self.session_service = InMemorySessionService()
        self.agent_client = Runner(
            app_name=self.app_name,
            agent=agent,
            session_service=self.session_service,
        )
        self.current_session_id = None

    async def async_init(self):
        """Initialize async resources."""
        session = await self.session_service.create_session(
            app_name=self.app_name, user_id=self.user_id
        )
        self.current_session_id = session.id
        return self

    async def run(self, prompt: str) -> List[Event]:
        """Run agent with the given prompt and return all events."""
        current_session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=self.user_id,
            session_id=self.current_session_id,
        )
        assert current_session is not None

        events = []
        async for event in self.agent_client.run_async(
            user_id=current_session.user_id,
            session_id=current_session.id,
            new_message=types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)],
            ),
        ):
            events.append(event)

        return events

    async def get_events(self) -> List[Event]:
        """Get all events from current session."""
        current_session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=self.user_id,
            session_id=self.current_session_id,
        )
        return current_session.events

    async def get_final_response_text(self) -> str:
        """Get the final response text from the last model response."""
        events = await self.get_events()
        for event in reversed(events):
            if event.content and event.content.role == "model":
                if event.content.parts and event.content.parts[0].text:
                    return event.content.parts[0].text.strip()
        return ""

    async def get_tool_calls_sequence(self) -> List[str]:
        """Extract the sequence of tool calls from events."""
        tool_calls = []
        events = await self.get_events()

        for event in events:
            if event.content and event.content.role == "model":
                for part in event.content.parts:
                    if part.function_call:
                        tool_calls.append(part.function_call.name)

        return tool_calls

    def extract_final_answer(self, text: str) -> str:
        """Extract content from <final_answer>...</final_answer> tags."""
        match = re.search(r"<final_answer>(.*?)</final_answer>", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return ""


@pytest.fixture
def agent():
    """Load the sample_reward_func agent."""
    import sys

    # Add examples directory to Python path
    examples_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
        "examples",
        "agents_with_features",
    )
    sys.path.insert(0, examples_dir)

    # Import the agent module
    from sample_reward_func import agent as agent_module

    yield agent_module.root_agent

    # Cleanup: Remove logger handlers to avoid "I/O operation on closed file" errors
    logger.remove()


@pytest.fixture
def logs_dir():
    """Get the logs directory path and ensure cleanup after tests."""
    examples_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
        "examples",
        "agents_with_features",
        "sample_reward_func",
    )
    logs_dir = Path(examples_dir) / ".logs"

    yield logs_dir

    # Cleanup: Remove .logs directory if it exists
    if logs_dir.exists():
        try:
            shutil.rmtree(logs_dir)
        except Exception as e:
            print(f"Warning: Failed to clean up logs directory: {e}")


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.asyncio
async def test_reward_func_calculation(agent, logs_dir):
    """
    Test RewardLogger with mathematical calculation.

    Verifies:
    1. Agent correctly calculates 2+3+6 = 11
    2. Tool calls include add_numbers
    3. Final answer in <final_answer> tags equals "11"
    4. RewardLogger creates log files in both tool_rewards and agent_rewards directories
    5. Logs are cleaned up after test
    """
    runner = await RewardFuncTestRunner(agent).async_init()

    # Run the agent with the test query
    events = await runner.run("calculate 2+3+6")

    # Get tool call sequence
    tool_calls = await runner.get_tool_calls_sequence()

    # Verify add_numbers tool was called
    assert "add_numbers" in tool_calls, (
        f"Expected 'add_numbers' in tool calls, got: {tool_calls}"
    )

    # Get final response
    final_response = await runner.get_final_response_text()

    # Extract answer from <final_answer> tags
    final_answer = runner.extract_final_answer(final_response)

    # Verify the answer is 11 (2+3+6)
    assert final_answer == "11", (
        f"Expected final answer to be '11', got: '{final_answer}' "
        f"(full response: {final_response})"
    )

    # Verify that reward logs were created
    assert logs_dir.exists(), "Expected .logs directory to be created"

    # Check for tool_rewards and agent_rewards subdirectories
    tool_rewards_dir = logs_dir / "tool_rewards"
    agent_rewards_dir = logs_dir / "agent_rewards"

    assert tool_rewards_dir.exists(), (
        "Expected tool_rewards directory to exist "
        "(agent has 4 tool-based RewardLoggers)"
    )
    assert agent_rewards_dir.exists(), (
        "Expected agent_rewards directory to exist "
        "(agent has 2 agent-based RewardLoggers)"
    )

    # Verify log files contain data
    log_files = list(logs_dir.rglob("*.jsonl"))
    assert len(log_files) > 0, "Expected at least one log file to be created"

    # Check that log files are not empty
    for log_file in log_files:
        file_size = log_file.stat().st_size
        assert file_size > 0, f"Log file {log_file} should not be empty"

    # Verify tool_rewards log content
    tool_log_files = list(tool_rewards_dir.rglob("*.jsonl"))
    assert len(tool_log_files) > 0, "Expected tool reward log files"

    tool_reward_entries = []
    for log_file in tool_log_files:
        with open(log_file, "r") as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    tool_reward_entries.append(entry)

    # Should have 2 tool reward entries (for 2 add_numbers calls)
    assert len(tool_reward_entries) >= 2, (
        f"Expected at least 2 tool reward entries for add_numbers calls, "
        f"got {len(tool_reward_entries)}"
    )

    # Verify structure of tool reward entries
    for entry in tool_reward_entries:
        assert "timestamp" in entry, "Tool reward entry should have timestamp"
        assert "reward_type" in entry, "Tool reward entry should have reward_type"
        assert entry["reward_type"] == "intermediate_reward", (
            "Tool rewards should be intermediate_reward type"
        )
        assert "reward_value" in entry, "Tool reward entry should have reward_value"
        assert isinstance(entry["reward_value"], (int, float)), (
            "Reward value should be numeric"
        )
        assert "reward_function_name" in entry, (
            "Tool reward entry should have reward_function_name"
        )
        assert "tool_name" in entry, "Tool reward entry should have tool_name"
        assert entry["tool_name"] == "add_numbers", (
            f"Expected tool_name to be 'add_numbers', got {entry['tool_name']}"
        )
        assert "tool_args" in entry, "Tool reward entry should have tool_args"
        assert "tool_result" in entry, "Tool reward entry should have tool_result"

        # Verify tool_result structure
        tool_result = entry["tool_result"]
        assert "operation" in tool_result, "Tool result should have operation"
        assert tool_result["operation"] == "addition", "Operation should be addition"
        assert "result" in tool_result, "Tool result should have result"
        assert "status" in tool_result, "Tool result should have status"
        assert tool_result["status"] == "completed", (
            "Tool execution should be completed"
        )
        assert "is_positive" in tool_result, "Tool result should have is_positive"
        assert tool_result["is_positive"] is True, (
            "Result of 2+3 or 5+6 should be positive"
        )

    # Verify agent_rewards log content
    agent_log_files = list(agent_rewards_dir.rglob("*.jsonl"))
    assert len(agent_log_files) > 0, "Expected agent reward log files"

    agent_reward_entries = []
    for log_file in agent_log_files:
        with open(log_file, "r") as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    agent_reward_entries.append(entry)

    # Should have 2 agent reward entries (one for each reward function)
    assert len(agent_reward_entries) >= 2, (
        f"Expected at least 2 agent reward entries, got {len(agent_reward_entries)}"
    )

    # Verify structure of agent reward entries
    reward_function_names = set()
    for entry in agent_reward_entries:
        assert "timestamp" in entry, "Agent reward entry should have timestamp"
        assert "reward_type" in entry, "Agent reward entry should have reward_type"
        assert entry["reward_type"] == "final_reward", (
            "Agent rewards should be final_reward type"
        )
        assert "reward_value" in entry, "Agent reward entry should have reward_value"
        assert isinstance(entry["reward_value"], (int, float)), (
            "Reward value should be numeric"
        )
        assert "reward_function_name" in entry, (
            "Agent reward entry should have reward_function_name"
        )
        reward_function_names.add(entry["reward_function_name"])
        assert "agent_name" in entry, "Agent reward entry should have agent_name"
        assert entry["agent_name"] == "math_reward_demo_agent", (
            f"Expected agent_name to be 'math_reward_demo_agent', "
            f"got {entry['agent_name']}"
        )
        assert "agent_response" in entry, (
            "Agent reward entry should have agent_response"
        )
        # Verify agent response contains the final answer
        assert "<final_answer>11</final_answer>" in entry["agent_response"], (
            "Agent response should contain <final_answer>11</final_answer>"
        )

    # Verify both reward functions were called
    expected_functions = {
        "explanation_quality_reward",
        "mathematical_accuracy_reward",
    }
    assert reward_function_names == expected_functions, (
        f"Expected reward functions {expected_functions}, got {reward_function_names}"
    )
