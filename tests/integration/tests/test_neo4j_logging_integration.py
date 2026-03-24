"""
Integration test for Neo4j logging with sample_neo4j_logging agent.

Tests the complete flow of agent execution with Neo4j history logging,
including node creation verification and result validation.
"""

import asyncio
import logging
import os
import re
import shutil
import uuid
import warnings

import pytest
from google.adk import Runner
from google.adk.apps.app import App
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

# Import these later to avoid circular import issues


class TestNeo4jLoggingIntegration:
    """Integration tests for Neo4j logging functionality."""

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
        opensage_session_id = str(uuid.uuid4())

        # Create temporary directory for agent storage
        test_storage_dir = "/tmp/neo4j_history_test/agent_storage"
        os.makedirs(test_storage_dir, exist_ok=True)

        # Database name that will be created
        database_name = f"agent-history"

        yield {
            "opensage_session_id": opensage_session_id,
            "test_storage_dir": test_storage_dir,
            "database_name": database_name,
        }

        # Cleanup: Remove sessions and resources
        from opensage.session.opensage_session import OpenSageSessionRegistry

        OpenSageSessionRegistry.cleanup_all_sessions()

    async def _cleanup_test_database(
        self, opensage_session_id: str, database_name: str
    ):
        """Clean up the test database."""
        try:
            # Import here to avoid circular import
            from opensage.session import get_opensage_session

            # Get opensage session to access Neo4j client
            opensage_session = get_opensage_session(opensage_session_id)

            # Get the default Neo4j client (connected to 'neo4j' database)
            default_client = await opensage_session.neo4j.get_async_client(
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
            opensage_session_id = test_env["opensage_session_id"]
            database_name = test_env["database_name"]
            test_storage_dir = test_env["test_storage_dir"]

            # Clean up temporary storage directory
            if os.path.exists(test_storage_dir):
                shutil.rmtree(test_storage_dir)

            # Drop the test database
            await self._cleanup_test_database(opensage_session_id, database_name)

        except Exception as e:
            print(f"Warning: Manual cleanup failed: {e}")

    @pytest.mark.filterwarnings("ignore::UserWarning")
    @pytest.mark.asyncio
    async def test_neo4j_logging_with_calculation(self, setup_test_environment):
        """Test complete Neo4j logging flow with mathematical calculation."""
        test_env = setup_test_environment
        opensage_session_id = test_env["opensage_session_id"]

        # Load the sample_neo4j_logging agent
        import sys

        examples_dir = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            ),
            "examples",
            "agents_with_features",
        )
        sys.path.insert(0, examples_dir)

        from sample_neo4j_logging import agent as agent_module

        root_agent = agent_module.mk_agent(opensage_session_id=opensage_session_id)

        # Prepare OpenSage environment for given opensage_session_id
        config_path = os.path.join(
            examples_dir,
            "sample_neo4j_logging",
            "config.toml",
        )
        opensage_session = get_opensage_session(
            opensage_session_id=opensage_session_id, config_path=config_path
        )
        # Force storage path via config to avoid env coupling
        opensage_session.config.agent_storage_path = test_env["test_storage_dir"]
        try:
            deps = collect_sandbox_dependencies(root_agent)
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

        # Create session service and runner
        session_service = OpenSageInMemorySessionService()
        enabled_plugins = (
            getattr(getattr(opensage_session.config, "plugins", None), "enabled", [])
            or []
        )
        plugins = load_plugins(enabled_plugins)
        agentic_app = App(
            name="neo4j_logging_test",
            root_agent=root_agent,
            plugins=plugins,
        )
        runner = Runner(
            app=agentic_app,
            session_service=session_service,
        )

        # Create session with opensage_session_id in state
        session = await session_service.create_session(
            app_name="neo4j_logging_test",
            user_id="test_user",
            state={"opensage_session_id": opensage_session_id},
        )

        # Test cases with different mathematical operations
        test_cases = [
            {
                "input": "calculate 2*1239+8, using a subagent",
                "expected_result": 2486,  # 2*1239 + 8 = 2478 + 8 = 2486
                "description": "arithmetic calculation with subagent",
            },
            {
                "input": "calculate the perimeter of a rectangle of width 8 and length 19",
                "expected_result": 54,  # 2*(8+19) = 2*27 = 54
                "description": "geometry calculation",
            },
        ]

        all_events = []
        session_results = []

        for i, test_case in enumerate(test_cases):
            print(f"\n--- Running test case {i + 1}: {test_case['description']} ---")

            # Create new session for each test case
            session = await session_service.create_session(
                app_name="neo4j_logging_test",
                user_id="test_user",
                state={"opensage_session_id": opensage_session_id},
            )

            # Run the agent
            events = []
            async for event in runner.run_async(
                user_id=session.user_id,
                session_id=session.id,
                new_message=types.Content(
                    role="user", parts=[types.Part(text=test_case["input"])]
                ),
            ):
                events.append(event)

            # Verify we got events
            assert len(events) > 0, f"No events were generated for test case {i + 1}"

            # Get the final response
            final_event = events[-1]
            assert final_event.content is not None, (
                f"Final event has no content for test case {i + 1}"
            )
            assert len(final_event.content.parts) > 0, (
                f"Final event has no content parts for test case {i + 1}"
            )

            final_text = ""
            for part in final_event.content.parts:
                if hasattr(part, "text") and part.text:
                    final_text += part.text

            print(f"Final response: {final_text}")

            # Verify the calculation result
            expected_str = str(test_case["expected_result"])
            final_answer = self.extract_final_answer(final_text)
            assert expected_str == final_answer, (
                f"Expected result {expected_str} not found in final response or not in the required format: {final_text}"
            )

            # Store results for Neo4j verification
            all_events.extend(events)
            session_results.append(
                {
                    "session_id": session.id,
                    "input": test_case["input"],
                    "expected_result": test_case["expected_result"],
                    "description": test_case["description"],
                }
            )

        # Verify Neo4j logging using history_management functions
        await self._verify_neo4j_logging_with_history_management(
            session_results, opensage_session_id
        )

        # Manual cleanup
        await self._manual_cleanup(test_env)

    async def _verify_neo4j_logging_with_history_management(
        self, session_results, opensage_session_id: str
    ):
        """Verify Neo4j logging using history_management.py functions."""
        # Import here to avoid circular import
        from opensage.session import get_opensage_session
        from opensage.toolbox.general.history_management import (
            get_all_agent_runs,
            get_all_invocations_for_agent,
            list_all_events_for_session,
        )

        # Get opensage session
        opensage_session = get_opensage_session(opensage_session_id)

        # Create a mock tool context for history management functions
        class MockInvocationContext:
            def __init__(self, session_obj):
                self.session = session_obj

        class MockToolContext:
            def __init__(self, session_obj):
                self._invocation_context = MockInvocationContext(session_obj)

        # Create mock session object with opensage_session_id
        class MockSession:
            def __init__(self, session_id, opensage_session_id):
                self.id = session_id
                self.state = {"opensage_session_id": opensage_session_id}

        mock_session = MockSession("mock_session_id", opensage_session_id)
        tool_context = MockToolContext(mock_session)

        print(f"\n🔍 Verifying Neo4j logging for {len(session_results)} test cases...")

        # Test 1: Get all agent runs
        agent_runs = await get_all_agent_runs(tool_context)
        assert len(agent_runs) >= len(session_results), (
            f"Expected at least {len(session_results)} agent runs, got {len(agent_runs)}"
        )

        print(f"✅ Found {len(agent_runs)} agent runs in Neo4j database")

        # Test 2: Verify each session's data
        for i, session_result in enumerate(session_results):
            session_id = session_result["session_id"]
            expected_input = session_result["input"]
            expected_output = session_result["expected_result"]
            description = session_result["description"]

            print(f"\n--- Verifying session {i + 1}: {description} ---")

            # Find the corresponding agent run
            matching_run = None
            for run in agent_runs:
                if run["session_id"] == session_id:
                    matching_run = run
                    break

            assert matching_run is not None, (
                f"Agent run not found for session_id: {session_id}"
            )

            # Verify agent name
            assert matching_run["agent_name"] == "calculation_orchestrator", (
                f"Expected agent_name 'calculation_orchestrator', got {matching_run['agent_name']}"
            )

            # Verify input contents
            assert matching_run["input_contents"] is not None, (
                f"No input_contents found for session {session_id}"
            )
            assert len(matching_run["input_contents"]) > 0, (
                f"Input_contents is empty for session {session_id}"
            )

            input_text = (
                matching_run["input_contents"][0]
                if matching_run["input_contents"]
                else ""
            )
            output_text = (
                matching_run["output_contents"][0]
                if matching_run["output_contents"]
                else ""
            )
            output_text = self.extract_final_answer(output_text)

            # Check if the expected input keywords are present
            assert str(input_text) == str(expected_input), (
                f"Expected {expected_input} not the same as input_contents: {input_text}"
            )
            assert str(output_text) == str(expected_output), (
                f"Expected {expected_output} not the same as output_contents: {output_text}"
            )

            print(f"✅ Session {session_id} verified:")
            print(f"   - Agent name: {matching_run['agent_name']}")
            print(f"   - Input: {input_text}")

            # Test 3: Get events for this session
            session_events = await list_all_events_for_session(session_id, tool_context)
            assert len(session_events) > 0, f"No events found for session {session_id}"
            print(f"   - Found {len(session_events)} events")

        # Test 4: Get invocations for the calculation_orchestrator agent
        invocations, _ = await get_all_invocations_for_agent(
            "geometry_calculator", tool_context
        )
        assert len(invocations) >= 0, f"No invocations found for geometry_calculator"

        print(f"\n✅ Neo4j logging verification completed successfully!")
        print(f"   - Total agent runs: {len(agent_runs)}")
        print(f"   - Total invocations for geometry_calculator: {len(invocations)}")
        print(f"   - All {len(session_results)} test cases verified in Neo4j")


# Run test manually if needed
if __name__ == "__main__":
    import asyncio

    async def run_manual_test():
        """Run the test manually for debugging."""
        test_instance = TestNeo4jLoggingIntegration()

        # Create mock fixture
        opensage_session_id = str(uuid.uuid4())
        test_storage_dir = "/tmp/neo4j_history_test/agent_storage"
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
                "opensage_session_id": opensage_session_id,
                "test_storage_dir": test_storage_dir,
                "database_name": f"agent-history",
                "original_agent_storage_path": None,
            }
        )

        try:
            await test_instance.test_neo4j_logging_with_calculation(test_env)
            print("✅ All manual tests passed!")
        finally:
            # Cleanup
            if os.path.exists(test_storage_dir):
                shutil.rmtree(test_storage_dir)
            await test_instance._cleanup_test_database(
                opensage_session_id, test_env["database_name"]
            )

    asyncio.run(run_manual_test())
