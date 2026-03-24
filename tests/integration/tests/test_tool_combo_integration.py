"""
Test ToolCombo functionality with return_history True and False.

This test verifies that:
1. ToolCombo with return_history=True shows intermediate steps and calls tools in the correct order
2. ToolCombo with return_history=False calls only the combo tool and returns final result
3. Both configurations produce the correct final answer
"""

import importlib
import logging
import re
import uuid
import warnings
from typing import List

import pytest
from google.adk import Runner
from google.adk.apps.app import App
from google.adk.events import Event
from google.genai import types

from opensage.features.opensage_in_memory_session_service import (
    OpenSageInMemorySessionService,
)
from opensage.plugins import load_plugins
from opensage.session import get_opensage_session
from opensage.toolbox.sandbox_requirements import collect_sandbox_dependencies

logger = logging.getLogger(__name__)

# Filter out Pydantic serialization warnings from LiteLLM
warnings.filterwarnings("ignore", message=".*Pydantic serializer warnings.*")
warnings.filterwarnings("ignore", message=".*PydanticSerializationUnexpectedValue.*")


class ToolComboTestRunner:
    """Test runner specifically for ToolCombo tests."""

    app_name = "tool_combo_test"
    user_id = "test_user"

    def __init__(self, agent):
        self.agent = agent
        self.session_service = OpenSageInMemorySessionService()
        self.agent_client = None
        self.current_session_id = str(uuid.uuid4())

    async def async_init(self):
        """Initialize OpenSage env and ADK session."""
        # Prepare OpenSage environment
        opensage_session = get_opensage_session(
            opensage_session_id=self.current_session_id, config_path=None
        )
        # Force storage path via config to avoid env coupling
        opensage_session.config.agent_storage_path = (
            "/tmp/tool_combo_test/agent_storage"
        )
        try:
            deps = collect_sandbox_dependencies(self.agent)
            if (
                opensage_session.config.sandbox
                and opensage_session.config.sandbox.sandboxes
                and deps
            ):
                unused = [
                    s
                    for s in list(opensage_session.config.sandbox.sandboxes.keys())
                    if s not in deps
                ]
                for s in unused:
                    del opensage_session.config.sandbox.sandboxes[s]
        except Exception:
            pass
        opensage_session.sandboxes.initialize_shared_volumes()
        await opensage_session.sandboxes.launch_all_sandboxes()
        await opensage_session.sandboxes.initialize_all_sandboxes(
            continue_on_error=True
        )

        enabled_plugins = (
            getattr(getattr(opensage_session.config, "plugins", None), "enabled", [])
            or []
        )
        plugins = load_plugins(enabled_plugins)
        agentic_app = App(
            name=self.app_name,
            root_agent=self.agent,
            plugins=plugins,
        )
        self.agent_client = Runner(
            app=agentic_app,
            session_service=self.session_service,
        )

        await self.session_service.create_session(
            app_name=self.app_name,
            user_id=self.user_id,
            session_id=self.current_session_id,
            state={"opensage_session_id": self.current_session_id},
        )
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
    """Load the sample_tool_combo agent."""
    import os
    import sys
    import uuid

    from google.adk.models.lite_llm import LiteLlm

    from opensage.features.agent_history_tracker import disable_neo4j_logging

    # Disable Neo4j logging for this non-Neo4j test
    try:
        disable_neo4j_logging()
    except Exception:
        pass

    # Add examples directory to Python path
    examples_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
        "examples",
        "agents_with_features",
    )
    sys.path.insert(0, examples_dir)

    # Import the agent module
    from sample_tool_combo import agent as agent_module

    agent_instance = agent_module.mk_agent(opensage_session_id=str(uuid.uuid4()))
    yield agent_instance

    # Cleanup: Remove sessions and resources
    from opensage.session.opensage_session import OpenSageSessionRegistry

    OpenSageSessionRegistry.cleanup_all_sessions()


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.asyncio
async def test_tool_combo_with_history(agent):
    """
    Test ToolCombo with return_history=True.

    Verifies:
    1. Tool calls follow the sequence: transfer_to_agent, add_numbers, multiply_by_two, delegate_to_parent
    2. Final answer in <final_answer> tags equals "10"
    """
    runner = await ToolComboTestRunner(agent).async_init()

    # Run the agent with the test query
    events = await runner.run("calculate (2+3)*2, with history, use the tool combo")

    # Get tool call sequence
    tool_calls = await runner.get_tool_calls_sequence()

    # Verify tool call sequence
    # Expected: transfer_to_agent (to simple_combo_with_history),
    # then add_numbers, multiply_by_two, delegate_to_parent
    assert "transfer_to_agent" in tool_calls, (
        f"Expected 'transfer_to_agent' in tool calls, got: {tool_calls}"
    )
    assert "add_numbers" in tool_calls, (
        f"Expected 'add_numbers' in tool calls, got: {tool_calls}"
    )
    assert "multiply_by_two" in tool_calls, (
        f"Expected 'multiply_by_two' in tool calls, got: {tool_calls}"
    )
    assert "delegate_to_parent" in tool_calls, (
        f"Expected 'delegate_to_parent' in tool calls, got: {tool_calls}"
    )

    # Verify the order is correct
    transfer_idx = tool_calls.index("transfer_to_agent")
    add_idx = tool_calls.index("add_numbers")
    multiply_idx = tool_calls.index("multiply_by_two")
    delegate_idx = tool_calls.index("delegate_to_parent")

    assert transfer_idx < add_idx < multiply_idx < delegate_idx, (
        f"Tool calls not in correct order. Got sequence: {tool_calls}"
    )

    # Get final response
    final_response = await runner.get_final_response_text()

    # Extract answer from <final_answer> tags
    final_answer = runner.extract_final_answer(final_response)

    # Verify the answer is 10
    assert final_answer == "10", (
        f"Expected final answer to be '10', got: '{final_answer}' "
        f"(full response: {final_response})"
    )


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.asyncio
async def test_tool_combo_without_history(agent):
    """
    Test ToolCombo with return_history=False.

    Verifies:
    1. Only calls the combo tool (simple_combo_without_history), not individual tools
    2. Final answer in <final_answer> tags equals "10"
    """
    runner = await ToolComboTestRunner(agent).async_init()

    # Run the agent with the test query
    events = await runner.run("calculate (2+3)*2, without history, use the tool combo")

    # Get tool call sequence
    tool_calls = await runner.get_tool_calls_sequence()

    # Verify only one tool call (the combo tool itself)
    # When return_history=False, the SequentialAgent is wrapped in AgentTool,
    # so we should see only "simple_combo_without_history" being called
    assert "simple_combo_without_history" in tool_calls, (
        f"Expected 'simple_combo_without_history' in tool calls, got: {tool_calls}"
    )

    # Verify individual tools (add_numbers, multiply_by_two) are NOT in the top-level tool calls
    # They should be internal to the combo
    assert "add_numbers" not in tool_calls, (
        f"'add_numbers' should not be in top-level tool calls when return_history=False. "
        f"Got: {tool_calls}"
    )
    assert "multiply_by_two" not in tool_calls, (
        f"'multiply_by_two' should not be in top-level tool calls when return_history=False. "
        f"Got: {tool_calls}"
    )

    # Get final response
    final_response = await runner.get_final_response_text()

    # Extract answer from <final_answer> tags
    final_answer = runner.extract_final_answer(final_response)

    # Verify the answer is 10
    assert final_answer == "10", (
        f"Expected final answer to be '10', got: '{final_answer}' "
        f"(full response: {final_response})"
    )


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.asyncio
async def test_both_combos_produce_same_result(agent):
    """
    Verify that both ToolCombo configurations produce the same final result.

    This ensures that return_history only affects the visibility of intermediate steps,
    not the computation itself.
    """
    # Test with history
    runner_with_history = await ToolComboTestRunner(agent).async_init()
    await runner_with_history.run("calculate (2+3)*2, with history, use the tool combo")
    response_with_history = await runner_with_history.get_final_response_text()
    answer_with_history = runner_with_history.extract_final_answer(
        response_with_history
    )

    # Test without history
    runner_without_history = await ToolComboTestRunner(agent).async_init()
    await runner_without_history.run(
        "calculate (2+3)*2, without history, use the tool combo"
    )
    response_without_history = await runner_without_history.get_final_response_text()
    answer_without_history = runner_without_history.extract_final_answer(
        response_without_history
    )

    # Both should produce "10"
    assert answer_with_history == answer_without_history == "10", (
        f"Expected both configurations to produce '10'. "
        f"With history: '{answer_with_history}', Without history: '{answer_without_history}'"
    )
