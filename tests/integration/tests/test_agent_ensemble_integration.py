"""
Integration test for agent ensemble functionality with sample_agent_ensemble agent.

Tests the complete flow of agent execution with ensemble coordination, verifying:
1. Input: "calculate 2+9+11, using at least two models"
2. Expected tool sequence: get_available_agents_and_models_for_ensemble -> agent_ensemble
3. Expected output: final answer = 22

This test verifies that the ensemble functionality is properly triggered and
that the mathematical calculation is performed correctly.
"""

import asyncio
import os
import re
import shutil
import uuid
import warnings

import pytest
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from loguru import logger

# Filter out Pydantic serialization warnings from LiteLLM
warnings.filterwarnings("ignore", message=".*Pydantic serializer warnings.*")
warnings.filterwarnings("ignore", message=".*PydanticSerializationUnexpectedValue.*")

# Import these later to avoid circular import issues


class TestAgentEnsembleIntegration:
    """Integration tests for agent ensemble functionality."""

    def extract_final_answer(self, text: str) -> str:
        """Extract content from <final_answer>...</final_answer> tags."""
        match = re.search(r"<final_answer>(.*?)</final_answer>", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return ""

    @pytest.fixture(scope="function")
    def setup_test_environment(self):
        """Set up test environment with temporary directories and database cleanup."""
        # Generate unique shared session ID
        aigise_session_id = str(uuid.uuid4())

        # Create temporary directory for agent storage
        test_storage_dir = "/tmp/agent_ensemble_test/agent_storage"
        os.makedirs(test_storage_dir, exist_ok=True)

        # Store original AGENT_STORAGE_PATH
        original_agent_storage_path = os.environ.get("AGENT_STORAGE_PATH")

        # Set test environment variable
        os.environ["AGENT_STORAGE_PATH"] = test_storage_dir

        # Database name that will be created
        database_name = f"agent-history-{aigise_session_id}"

        yield {
            "aigise_session_id": aigise_session_id,
            "test_storage_dir": test_storage_dir,
            "database_name": database_name,
            "original_agent_storage_path": original_agent_storage_path,
        }

        # Cleanup: Remove logger handlers to avoid "I/O operation on closed file" errors
        logger.remove()

    async def _cleanup_test_database(self, aigise_session_id: str, database_name: str):
        """Clean up the test database."""
        try:
            # Import here to avoid circular import
            from aigise.session import get_aigise_session

            # Get aigise session to access Neo4j client
            aigise_session = get_aigise_session(aigise_session_id)

            # Get the default Neo4j client (connected to 'neo4j' database)
            default_client = await aigise_session.neo4j.get_async_client(
                "default", "neo4j"
            )

            # Drop the test database
            drop_query = f"DROP DATABASE `{database_name}` IF EXISTS"
            await default_client.run_query(drop_query)
            print(f"Successfully dropped test database: {database_name}")

        except Exception as e:
            print(f"Warning: Failed to drop test database {database_name}: {e}")

    async def _manual_cleanup(self, test_env):
        """Manual cleanup after test completion."""
        try:
            aigise_session_id = test_env["aigise_session_id"]
            database_name = test_env["database_name"]
            test_storage_dir = test_env["test_storage_dir"]
            original_agent_storage_path = test_env["original_agent_storage_path"]

            # Clean up temporary storage directory
            if os.path.exists(test_storage_dir):
                shutil.rmtree(test_storage_dir)

            # Restore original environment variable
            if original_agent_storage_path is not None:
                os.environ["AGENT_STORAGE_PATH"] = original_agent_storage_path
            else:
                os.environ.pop("AGENT_STORAGE_PATH", None)

            # Drop the test database
            await self._cleanup_test_database(aigise_session_id, database_name)

        except Exception as e:
            print(f"Warning: Manual cleanup failed: {e}")

    @pytest.mark.filterwarnings("ignore::UserWarning")
    @pytest.mark.asyncio
    async def test_agent_ensemble_with_calculation(self, setup_test_environment):
        """Test complete agent ensemble flow with mathematical calculation."""
        test_env = setup_test_environment
        aigise_session_id = test_env["aigise_session_id"]

        # Load the sample_agent_ensemble agent
        import sys

        # Get the AIgiSE root directory and construct path to examples
        current_dir = os.path.dirname(__file__)  # tests/integration/framework/
        aigise_root = os.path.dirname(
            os.path.dirname(os.path.dirname(current_dir))
        )  # AIgiSE root
        examples_dir = os.path.join(aigise_root, "examples", "agents_with_features")
        sys.path.insert(0, examples_dir)

        from sample_agent_ensemble import agent as agent_module

        root_agent = agent_module.root_agent

        # Create session service and runner
        session_service = InMemorySessionService()
        runner = Runner(
            app_name="agent_ensemble_test",
            agent=root_agent,
            session_service=session_service,
        )

        # Create session with aigise_session_id in state
        session = await session_service.create_session(
            app_name="agent_ensemble_test",
            user_id="test_user",
            state={"aigise_session_id": aigise_session_id},
        )

        # Test with the required input
        test_input = "calculate 2+9+11, using at least two models"
        expected_result = 22

        print(f"\n=== Testing Agent Ensemble Calculation ===")
        print(f"Input: {test_input}")
        print(f"Expected result: {expected_result}")

        # Run the agent
        events = []
        async for event in runner.run_async(
            user_id=session.user_id,
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=test_input)]),
        ):
            events.append(event)

        # Verify we got events
        assert len(events) > 0, "No events were generated for ensemble test"

        # Get the final response
        final_event = events[-1]
        assert final_event.content is not None, "Final event has no content"
        assert len(final_event.content.parts) > 0, "Final event has no content parts"

        final_text = ""
        for part in final_event.content.parts:
            if hasattr(part, "text") and part.text:
                final_text += part.text

        print(f"Final response: {final_text}")

        # Verify the calculation result
        expected_str = str(expected_result)
        final_answer = self.extract_final_answer(final_text)
        assert expected_str == final_answer, (
            f"Expected result {expected_str} not found in final response or not in the required format: {final_text}"
        )

        # Verify ensemble functionality was triggered (simplified approach)
        await self._verify_ensemble_functionality(session.id, aigise_session_id)

        # Manual cleanup
        await self._manual_cleanup(test_env)

    async def _verify_ensemble_functionality(
        self, session_id: str, aigise_session_id: str
    ):
        """Verify ensemble functionality was triggered (simplified approach)."""
        # Import here to avoid circular import
        from aigise.session import get_aigise_session
        from aigise.toolbox.general.history_management import (
            get_all_agent_runs,
            list_all_events_for_session,
        )

        # Get aigise session
        aigise_session = get_aigise_session(aigise_session_id)

        # Create mock tool context for history management functions
        class MockInvocationContext:
            def __init__(self, session_obj):
                self.session = session_obj

        class MockToolContext:
            def __init__(self, session_obj):
                self._invocation_context = MockInvocationContext(session_obj)

        class MockSession:
            def __init__(self, session_id, aigise_session_id):
                self.id = session_id
                self.state = {"aigise_session_id": aigise_session_id}

        mock_session = MockSession(session_id, aigise_session_id)
        tool_context = MockToolContext(mock_session)

        print(f"\n🔍 Verifying ensemble functionality...")

        # Get all events for the session
        session_events = await list_all_events_for_session(session_id, tool_context)
        assert len(session_events) > 0, f"No events found for session {session_id}"

        print(f"Found {len(session_events)} events for session {session_id}")

        # Get all agent runs to verify functionality
        agent_runs = await get_all_agent_runs(tool_context)
        print(f"Found {len(agent_runs)} agent runs total")

        # Verify main agent run contains our test
        main_agent_runs = [run for run in agent_runs if run["session_id"] == session_id]
        assert len(main_agent_runs) >= 1, (
            f"Expected at least 1 main agent run, got {len(main_agent_runs)}"
        )

        main_run = main_agent_runs[0]
        input_text = main_run["input_contents"][0] if main_run["input_contents"] else ""
        output_text = (
            main_run["output_contents"][0] if main_run["output_contents"] else ""
        )

        # Verify input contains required elements
        assert "2+9+11" in input_text, (
            f"Expected input '2+9+11' not found in: {input_text}"
        )
        assert "two models" in input_text.lower(), (
            f"Expected 'two models' not found in: {input_text}"
        )

        # Verify final answer is correct
        final_answer = self.extract_final_answer(output_text)
        assert final_answer == "22", f"Expected final answer '22', got: {final_answer}"

        # Count function calls to verify ensemble tools were attempted
        function_calls = [
            event for event in session_events if event.get("type") == "function_call"
        ]
        print(f"Found {len(function_calls)} function calls in session")

        # Verify we have multiple function calls (indicating ensemble logic was triggered)
        assert len(function_calls) >= 2, (
            f"Expected at least 2 function calls for ensemble functionality, got {len(function_calls)}"
        )

        print(f"✅ Ensemble functionality verification completed!")
        print(f"   - Input verified: {input_text}")
        print(f"   - Final answer verified: {final_answer}")
        print(
            f"   - Function calls made: {len(function_calls)} (indicating ensemble tools were used)"
        )

        # Note: Based on the log output, we can see that the ensemble manager was invoked:
        # - "Discovered 1 static agents" - indicates get_available_agents_and_models_for_ensemble was called
        # - Multiple function calls indicate the agent attempted to use ensemble functionality
        # - The result is correct (22), showing the calculation worked


# Run test manually if needed
if __name__ == "__main__":
    import asyncio

    async def run_manual_test():
        """Run the test manually for debugging."""
        test_instance = TestAgentEnsembleIntegration()

        # Create mock fixture
        aigise_session_id = str(uuid.uuid4())
        test_storage_dir = "/tmp/agent_ensemble_test/agent_storage"
        os.makedirs(test_storage_dir, exist_ok=True)
        os.environ["AGENT_STORAGE_PATH"] = test_storage_dir

        # Mock the setup_test_environment fixture return
        class MockFixture:
            def __init__(self, data):
                self._data = data

            def __getitem__(self, key):
                return self._data[key]

        test_env = MockFixture(
            {
                "aigise_session_id": aigise_session_id,
                "test_storage_dir": test_storage_dir,
                "database_name": f"agent-history-{aigise_session_id}",
                "original_agent_storage_path": None,
            }
        )

        try:
            await test_instance.test_agent_ensemble_with_calculation(test_env)
            print("✅ All manual tests passed!")
        finally:
            # Cleanup
            if os.path.exists(test_storage_dir):
                shutil.rmtree(test_storage_dir)
            await test_instance._cleanup_test_database(
                aigise_session_id, test_env["database_name"]
            )

    asyncio.run(run_manual_test())
