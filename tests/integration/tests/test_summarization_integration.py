"""
Integration test for summarization functionality with sample_summarization agent.

Tests the complete flow of agent execution with Neo4j history logging and summarization,
including tool response summarization and history summarization.
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

# Increase timeout for this integration file (Neo4j cold start + summarization)
pytestmark = pytest.mark.timeout(900)


class TestSummarizationIntegration:
    """Integration tests for summarization functionality."""

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
        test_storage_dir = "/tmp/summarization_test/agent_storage"
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
    async def test_summarization_with_calculation(self, setup_test_environment):
        """Test complete summarization flow with two isolated test cases."""
        test_env = setup_test_environment

        session_results = []

        # Test Case 1: Geometric calculation with tool summarization
        print("\n=== TEST CASE 1: Geometric area calculation ===")
        session_result_1 = await self._run_isolated_test_case(
            {
                "input": "calculate the area of a rectangle of length 19 and width 18, you should use the geometry_calculator tool",
                "expected_result": 342,  # 19 * 18 = 342
                "description": "geometric area calculation with tool summarization",
            },
            test_env["test_storage_dir"],
        )
        session_results.append(session_result_1)

        # Test Case 2: Arithmetic calculation with history summarization
        print("\n=== TEST CASE 2: Multiplication calculation ===")

        session_result_2 = await self._run_isolated_test_case(
            {
                "input": "calculate 21*29*23, you should use the math_calculator subagent",
                "expected_result": 14007,  # 21 * 29 * 23 = 14007
                "description": "multiplication calculation to trigger history summarization",
            },
            test_env["test_storage_dir"],
        )
        session_results.append(session_result_2)

        # Verify Neo4j logging and summarization for both test cases
        await self._verify_summarization_with_history_management(session_results)

        # Clean up isolated databases for each test case
        for session_result in session_results:
            try:
                opensage_session_id = session_result["opensage_session_id"]
                database_name = f"agent-history"
                await self._cleanup_test_database(opensage_session_id, database_name)
                print(f"✅ Cleaned up isolated database: {database_name}")
            except Exception as e:
                print(
                    f"⚠️  Warning: Failed to clean up isolated database for {session_result['description']}: {e}"
                )

        # Manual cleanup
        await self._manual_cleanup(test_env)

    async def _run_isolated_test_case(self, test_case, storage_dir: str):
        """Run a single test case with completely isolated environment."""
        # Generate unique shared session ID for this test case
        isolated_opensage_session_id = str(uuid.uuid4())

        print(f"Running isolated test case: {test_case['description']}")
        print(f"Isolated opensage_session_id: {isolated_opensage_session_id}")

        # Load the sample_summarization agent
        import sys

        examples_dir = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            ),
            "examples",
            "agents_with_features",
        )
        sys.path.insert(0, examples_dir)

        from sample_summarization import agent as agent_module

        root_agent = agent_module.mk_agent(
            opensage_session_id=isolated_opensage_session_id
        )

        # Prepare OpenSage environment for this isolated test case
        config_path = os.path.join(
            examples_dir,
            "sample_summarization",
            "config.toml",
        )
        opensage_session = get_opensage_session(
            opensage_session_id=isolated_opensage_session_id, config_path=config_path
        )
        # Force storage path via config to avoid env coupling
        opensage_session.config.agent_storage_path = storage_dir
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

        # Create completely isolated session service and runner
        session_service = OpenSageInMemorySessionService()
        enabled_plugins = (
            getattr(getattr(opensage_session.config, "plugins", None), "enabled", [])
            or []
        )
        plugins = load_plugins(enabled_plugins)
        agentic_app = App(
            name="summarization_test",
            root_agent=root_agent,
            plugins=plugins,
        )
        runner = Runner(
            app=agentic_app,
            session_service=session_service,
        )

        # Create session with isolated opensage_session_id
        session = await session_service.create_session(
            app_name="summarization_test",
            user_id="test_user",
            state={"opensage_session_id": isolated_opensage_session_id},
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
        assert len(events) > 0, (
            f"No events were generated for test case: {test_case['description']}"
        )

        # Get the final response
        final_event = events[-1]
        assert final_event.content is not None, (
            f"Final event has no content for test case: {test_case['description']}"
        )
        assert len(final_event.content.parts) > 0, (
            f"Final event has no content parts for test case: {test_case['description']}"
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

        # Return session result with isolated opensage_session_id for verification
        return {
            "session_id": session.id,
            "opensage_session_id": isolated_opensage_session_id,
            "input": test_case["input"],
            "expected_result": test_case["expected_result"],
            "description": test_case["description"],
        }

    async def _verify_summarization_with_history_management(self, session_results):
        """Verify summarization functionality using history_management.py functions."""
        # Import here to avoid circular import
        from opensage.session import get_opensage_session
        from opensage.toolbox.general.history_management import (
            get_all_agent_runs,
            get_all_invocations_for_agent,
            list_all_events_for_session,
        )

        # Create mock classes for tool context
        class MockInvocationContext:
            def __init__(self, session_obj):
                self.session = session_obj

        class MockToolContext:
            def __init__(self, session_obj):
                self._invocation_context = MockInvocationContext(session_obj)

        class MockSession:
            def __init__(self, session_id, opensage_session_id):
                self.id = session_id
                self.state = {"opensage_session_id": opensage_session_id}

        print(
            f"\n🔍 Verifying summarization functionality for {len(session_results)} isolated test cases..."
        )

        # Verify each test case independently in its own database
        for i, session_result in enumerate(session_results):
            opensage_session_id = session_result["opensage_session_id"]
            main_session_id = session_result["session_id"]
            expected_input = session_result["input"]
            expected_output = session_result["expected_result"]
            description = session_result["description"]

            print(f"\n--- Verifying test case {i + 1}: {description} ---")
            print(f"Database: agent-history")

            # Get opensage session for this specific test case
            opensage_session = get_opensage_session(opensage_session_id)
            mock_session = MockSession("mock_session_id", opensage_session_id)
            tool_context = MockToolContext(mock_session)

            # Test 1: Get agent runs for this specific test case
            agent_runs = await get_all_agent_runs(tool_context)
            print(f"   Found {len(agent_runs)} agent runs in this database")

            # Test 2: Find calculation_orchestrator and geometry_calculator runs
            calculation_orchestrator_runs = [
                run
                for run in agent_runs
                if run["agent_name"] == "calculation_orchestrator"
            ]
            geometry_calculator_runs = [
                run for run in agent_runs if run["agent_name"] == "geometry_calculator"
            ]

            assert len(calculation_orchestrator_runs) >= 1, (
                f"Expected at least 1 calculation_orchestrator run, got {len(calculation_orchestrator_runs)}"
            )

            # For test case 1 (geometry), verify geometry_calculator exists
            if i == 0 and "rectangle" in expected_input:
                assert len(geometry_calculator_runs) >= 1, (
                    "Expected at least 1 geometry_calculator run for geometric calculation"
                )

                # Test 3: Verify geometry_calculator has raw tool response relation
                geometry_session_id = geometry_calculator_runs[0]["session_id"]
                client = await opensage_session.neo4j.get_async_client("history")
                raw_tool_response_query = """
                MATCH (a:AgentRun {session_id: $session_id})-[:AGENT_RUN_HAS_RAW_TOOL_RESPONSE]->(r:RawToolResponse)
                RETURN r.node_id as node_id, r.tool_name as tool_name, r.raw_content as raw_content
                """

                raw_tool_responses = await client.run_query(
                    raw_tool_response_query, {"session_id": geometry_session_id}
                )

                assert len(raw_tool_responses) >= 1, (
                    f"Expected at least 1 raw tool response for geometry_calculator, got {len(raw_tool_responses)}"
                )

                print(
                    f"   ✅ geometry_calculator has {len(raw_tool_responses)} raw tool response(s)"
                )

                # Test 4: Verify geometry_calculator session has tool_response_summary event
                geometry_events = await list_all_events_for_session(
                    geometry_session_id, tool_context
                )

                tool_response_summary_events = [
                    event
                    for event in geometry_events
                    if event.get("type") == "tool_response_summary"
                ]

                assert len(tool_response_summary_events) >= 1, (
                    f"Expected at least 1 tool_response_summary event for geometry_calculator, got {len(tool_response_summary_events)}"
                )

                print(
                    f"   ✅ geometry_calculator has {len(tool_response_summary_events)} tool_response_summary event(s)"
                )

            # Test 5: Verify main orchestrator session input and output
            main_run = None
            for run in calculation_orchestrator_runs:
                if run["session_id"] == main_session_id:
                    main_run = run
                    break

            assert main_run is not None, (
                f"Main agent run not found for session_id: {main_session_id}"
            )

            # Verify input and output
            input_text = (
                main_run["input_contents"][0] if main_run["input_contents"] else ""
            )
            output_text = (
                main_run["output_contents"][0] if main_run["output_contents"] else ""
            )
            output_text = self.extract_final_answer(output_text)

            assert str(input_text) == str(expected_input), (
                f"Expected input '{expected_input}' not the same as input_contents: '{input_text}'"
            )
            assert str(output_text) == str(expected_output), (
                f"Expected output '{expected_output}' not the same as output_contents: '{output_text}'"
            )

            print(f"   ✅ Session {main_session_id} verified:")
            print(f"     - Agent name: {main_run['agent_name']}")
            print(f"     - Input: {input_text}")
            print(f"     - Output: {output_text}")

            # Test 6: Get main session events
            main_session_events = await list_all_events_for_session(
                main_session_id, tool_context
            )
            assert len(main_session_events) > 0, (
                f"No events found for main session {main_session_id}"
            )
            print(f"     - Found {len(main_session_events)} events in main session")

            # Test 7: For the second test case, verify history_summary event exists
            if i == 1 and "21*29*23" in expected_input:
                history_summary_events = [
                    event
                    for event in main_session_events
                    if event.get("type") == "history_summary"
                ]

                assert len(history_summary_events) >= 1, (
                    f"Expected at least 1 history_summary event for calculation_orchestrator in second test case, got {len(history_summary_events)}"
                )

                print(
                    f"     ✅ calculation_orchestrator has {len(history_summary_events)} history_summary event(s) for multiplication test"
                )

        print(f"\n✅ All isolated summarization tests completed successfully!")
        print(f"   - Verified {len(session_results)} independent test cases")
        print(f"   - Each test case used its own isolated database and session")
        print(f"   - Tool summarization verified for geometric calculation")
        print(f"   - History summarization verified for multiplication calculation")


# Run test manually if needed
if __name__ == "__main__":
    import asyncio

    async def run_manual_test():
        """Run the test manually for debugging."""
        test_instance = TestSummarizationIntegration()

        # Set up test environment
        test_storage_dir = "/tmp/summarization_test/agent_storage"
        os.makedirs(test_storage_dir, exist_ok=True)
        os.environ["AGENT_STORAGE_PATH"] = test_storage_dir
        original_agent_storage_path = None

        # Mock the setup_test_environment fixture return
        class MockFixture:
            def __init__(self, data):
                self._data = data

            def __getitem__(self, key):
                return self._data[key]

        test_env = MockFixture(
            {
                "test_storage_dir": test_storage_dir,
                "original_agent_storage_path": original_agent_storage_path,
            }
        )

        try:
            await test_instance.test_summarization_with_calculation(test_env)
            print("✅ All manual tests passed!")
        finally:
            # Cleanup
            if os.path.exists(test_storage_dir):
                shutil.rmtree(test_storage_dir)

            # Note: Individual test cases will clean up their own databases
            # since each one has its own isolated opensage_session_id

    asyncio.run(run_manual_test())
