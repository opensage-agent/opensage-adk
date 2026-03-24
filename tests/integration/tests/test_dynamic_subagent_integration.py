"""
Test Dynamic Subagent functionality with persistence.

This test verifies that:
1. Agent can dynamically create subagents with specific tools
2. Subagents persist across sessions
3. Tool call sequence is correct (list_active_agents, create_subagent, call_subagent_as_tool)
4. Mathematical calculations are correct
"""

import logging
import os
import re
import shutil
import uuid
import warnings
from pathlib import Path
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

# Test configuration
AGENT_STORAGE_PATH = "/tmp/dynamic_agent_test/agent_storage"

# Filter out Pydantic serialization warnings from LiteLLM
warnings.filterwarnings("ignore", message=".*Pydantic serializer warnings.*")
warnings.filterwarnings("ignore", message=".*PydanticSerializationUnexpectedValue.*")


class DynamicSubagentTestRunner:
    """Test runner for dynamic subagent tests."""

    app_name = "dynamic_subagent_test"
    user_id = "test_user"

    def __init__(self, agent):
        self.agent = agent
        self.session_service = OpenSageInMemorySessionService()
        self.agent_client = None
        # Pin a fixed session id so OpenSage env and ADK session align if needed
        self.current_session_id = str(uuid.uuid4())

    async def async_init(self):
        """Initialize async resources and prepare OpenSage environment."""
        # Prepare OpenSage environment (collect sandbox deps and init sandboxes)
        import os

        base_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        )
        config_path = os.path.join(
            base_dir,
            "examples",
            "agents_with_features",
            "sample_dynamic_subagent",
            "config.toml",
        )
        opensage_session = get_opensage_session(
            opensage_session_id=self.current_session_id, config_path=config_path
        )
        # Force storage path via config to avoid env coupling
        opensage_session.config.agent_storage_path = AGENT_STORAGE_PATH
        try:
            deps = collect_sandbox_dependencies(self.agent)
            if (
                opensage_session.config.sandbox
                and opensage_session.config.sandbox.sandboxes
                and deps
            ):
                # prune unused sandboxes
                unused = [
                    s
                    for s in list(opensage_session.config.sandbox.sandboxes.keys())
                    if s not in deps
                ]
                for s in unused:
                    del opensage_session.config.sandbox.sandboxes[s]
        except Exception:
            # If dependency collection fails, continue with config as-is
            pass
        # Initialize sandboxes
        opensage_session.sandboxes.initialize_shared_volumes()
        await opensage_session.sandboxes.launch_all_sandboxes()
        await opensage_session.sandboxes.initialize_all_sandboxes(
            continue_on_error=True
        )

        # Create ADK session with the fixed id
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
    """Load the sample_dynamic_subagent agent with custom storage path."""
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
    from sample_dynamic_subagent import agent as agent_module

    # Create agent with unique session ID
    agent_instance = agent_module.mk_agent(opensage_session_id=str(uuid.uuid4()))
    yield agent_instance

    # Cleanup: Remove sessions and resources
    from opensage.session.opensage_session import OpenSageSessionRegistry

    OpenSageSessionRegistry.cleanup_all_sessions()


@pytest.fixture(scope="session", autouse=True)
def setup_and_cleanup_storage():
    """Setup clean storage and environment before tests and cleanup after ALL tests."""
    storage_dir = Path(AGENT_STORAGE_PATH)

    # Clean up before tests start
    if storage_dir.exists():
        try:
            shutil.rmtree(storage_dir.parent)
        except Exception as e:
            print(f"Warning: Failed to clean up storage directory before tests: {e}")

    yield

    # Cleanup after all tests
    if storage_dir.exists():
        try:
            shutil.rmtree(storage_dir.parent)
        except Exception as e:
            print(f"Warning: Failed to clean up storage directory after tests: {e}")


@pytest.fixture
def storage_path():
    """Get the storage directory path for verification in tests."""
    return Path(AGENT_STORAGE_PATH)


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.asyncio
async def test_dynamic_subagent_creation_and_call(agent, storage_path):
    """
    Test dynamic subagent creation and execution in the first session.

    Verifies:
    1. Tool call sequence: list_active_agents -> create_subagent -> call_subagent_as_tool
    2. Agent correctly calculates 2 + 7986 * 191 = 1525328
    3. Final answer contains "1525328"
    4. Subagent metadata persists to storage
    """
    runner = await DynamicSubagentTestRunner(agent).async_init()

    # Run the agent with the test query
    events = await runner.run(
        "calculate 2+7986*191, you should use a subagent to do the calculation, if no subagent is found, you should create one"
    )

    # Get tool call sequence
    tool_calls = await runner.get_tool_calls_sequence()

    # Verify tool call sequence
    assert "list_active_agents" in tool_calls, (
        f"Expected 'list_active_agents' in tool calls, got: {tool_calls}"
    )
    assert "create_subagent" in tool_calls, (
        f"Expected 'create_subagent' in tool calls, got: {tool_calls}"
    )
    assert "call_subagent_as_tool" in tool_calls, (
        f"Expected 'call_subagent_as_tool' in tool calls, got: {tool_calls}"
    )

    assert not "add_numbers" in tool_calls, (
        f"Expected 'add_numbers' not to be in tool calls, since it should be called by the subagent: {tool_calls}"
    )

    assert not "multiply_numbers" in tool_calls, (
        f"Expected 'multiply_numbers' not to be in tool calls, since it should be called by the subagent: {tool_calls}"
    )

    # Verify the order is correct
    list_idx = tool_calls.index("list_active_agents")
    create_idx = tool_calls.index("create_subagent")
    call_idx = tool_calls.index("call_subagent_as_tool")

    assert list_idx < create_idx < call_idx, (
        f"Tool calls not in correct order. Got sequence: {tool_calls}"
    )

    # Get final response
    final_response = await runner.get_final_response_text()

    # Extract answer from <final_answer> tags
    final_answer = runner.extract_final_answer(final_response)

    # Verify the answer contains 1525328 (2 + 7986 * 191)
    assert "1525328" in final_answer, (
        f"Expected final answer to contain '1525328', got: '{final_answer}' "
        f"(full response: {final_response})"
    )

    # Verify that subagent metadata was persisted
    assert storage_path.exists(), "Expected storage directory to be created"
    metadata_files = list(storage_path.glob("*_metadata.json"))
    assert len(metadata_files) > 0, (
        "Expected at least one metadata file to be persisted"
    )


# @pytest.mark.filterwarnings("ignore::UserWarning")
# @pytest.mark.asyncio
# async def test_dynamic_subagent_persistence_across_sessions(agent, storage_path):
#     """
#     Test dynamic subagent persistence and loading in a new session.

#     Verifies:
#     1. Tool call sequence: list_active_agents -> call_subagent_as_tool (no create_subagent)
#     2. Agent correctly calculates 72138 * 82136 + 7 = 5925126775
#     3. Final answer contains "5925126775"
#     4. Subagent is loaded from persistence (not created)
#     """
#     # This test should run after test_dynamic_subagent_creation_and_call
#     # to ensure the subagent is already persisted
#     runner = await DynamicSubagentTestRunner(agent).async_init()

#     # Run the agent with a different calculation
#     await runner.run("calculate 72138*82136+7")

#     # Get tool call sequence
#     tool_calls = await runner.get_tool_calls_sequence()

#     # Verify tool call sequence
#     assert "list_active_agents" in tool_calls, (
#         f"Expected 'list_active_agents' in tool calls, got: {tool_calls}"
#     )
#     assert "call_subagent_as_tool" in tool_calls, (
#         f"Expected 'call_subagent_as_tool' in tool calls, got: {tool_calls}"
#     )

#     assert not "create_subagent" in tool_calls, (
#         f"Expected 'create_subagent' not to be in tool calls, since the subagent is already persisted: {tool_calls}"
#     )

#     assert not "add_numbers" in tool_calls, (
#         f"Expected 'add_numbers' not to be in tool calls, since it should be called by the subagent: {tool_calls}"
#     )

#     assert not "multiply_numbers" in tool_calls, (
#         f"Expected 'multiply_numbers' not to be in tool calls, since it should be called by the subagent: {tool_calls}"
#     )

#     assert tool_calls.index("list_active_agents") < tool_calls.index(
#         "call_subagent_as_tool"
#     ), (
#         f"Expected 'list_active_agents' to come before 'call_subagent_as_tool', got: {tool_calls}"
#     )

#     # Get final response
#     final_response = await runner.get_final_response_text()

#     # Extract answer from <final_answer> tags
#     final_answer = runner.extract_final_answer(final_response)

#     # Verify the answer contains 5925126775 (72138 * 82136 + 7)
#     assert "5925126775" in final_answer, (
#         f"Expected final answer to contain '5925126775', got: '{final_answer}' "
#         f"(full response: {final_response})"
#     )

#     # Verify that persisted subagent metadata still exists
#     assert storage_path.exists(), "Expected storage directory to exist"
#     metadata_files = list(storage_path.glob("*_metadata.json"))
#     assert len(metadata_files) > 0, "Expected persisted metadata files to still exist"
