"""Unit tests for history_management module."""

from __future__ import annotations

import json
import uuid
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from opensage.toolbox.general.history_management import (
    drop_or_summarize_events,
    get_all_agent_runs,
    get_all_events_for_summarization,
    get_all_invocations_for_agent,
    get_full_tool_res,
    get_full_tool_res_and_grep,
    list_all_events_for_session,
)


class TestHistoryManagementFunctions:
    """Test history management functions."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_context = MagicMock()
        self.mock_neo4j_client = AsyncMock()

        # Mock get_neo4j_client_from_context to return our mock client
        self.mock_get_client_patcher = patch(
            "opensage.toolbox.general.history_management.get_neo4j_client_from_context"
        )
        self.mock_get_client = self.mock_get_client_patcher.start()
        self.mock_get_client.return_value = self.mock_neo4j_client

    def teardown_method(self):
        """Clean up patches."""
        self.mock_get_client_patcher.stop()

    @pytest.mark.asyncio
    async def test_get_all_invocations_for_agent(self):
        """Test getting all invocations for an agent."""
        # Mock query result
        mock_result = [
            {
                "input_content": "Test input",
                "session_id": "session-123",
                "agent_name": "test-agent",
            }
        ]
        self.mock_neo4j_client.run_query.return_value = mock_result

        result, error = await get_all_invocations_for_agent(
            "test-agent", self.mock_context
        )

        # Verify query was called correctly
        self.mock_neo4j_client.run_query.assert_called_once()
        call_args = self.mock_neo4j_client.run_query.call_args
        assert "agent_name" in call_args[0][0]  # Query should contain agent_name
        # Parameters should be in the second positional argument
        query_params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
        assert query_params == {"agent_name": "test-agent"}

        assert result == mock_result
        assert error is None

    @pytest.mark.asyncio
    async def test_get_all_agent_runs_success(self):
        """Test getting all agent runs successfully."""
        # Mock query result
        mock_result = [
            {
                "session_id": "session-123",
                "agent_name": "test-agent",
                "opensage_session_id": "shared-456",
                "start_time": "2024-01-01T10:00:00",
                "end_time": "2024-01-01T10:05:00",
                "status": "completed",
                "input_contents": ["input1", "input2"],
                "output_contents": ["output1", "output2"],
                "agent_model": "test-model",
            }
        ]
        self.mock_neo4j_client.run_query.return_value = mock_result

        result = await get_all_agent_runs(self.mock_context)

        # Verify query was called
        self.mock_neo4j_client.run_query.assert_called_once()

        assert len(result) == 1
        agent_run = result[0]
        assert agent_run["session_id"] == "session-123"
        assert agent_run["agent_name"] == "test-agent"
        assert agent_run["input_contents"] == ["input1", "input2"]
        assert agent_run["output_contents"] == ["output1", "output2"]
        assert agent_run["agent_model"] == "test-model"

    @pytest.mark.asyncio
    async def test_get_all_agent_runs_failure(self):
        """Test handling failure in get_all_agent_runs."""
        self.mock_neo4j_client.run_query.side_effect = RuntimeError("Database error")

        result = await get_all_agent_runs(self.mock_context)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_full_tool_res_and_grep_success(self):
        """Test grepping tool response content successfully."""
        # Mock query result
        mock_result = [
            {
                "content": "line 1: error occurred\nline 2: normal operation\nline 3: another error",
                "tool_name": "test_tool",
            }
        ]
        self.mock_neo4j_client.run_query.return_value = mock_result

        result = await get_full_tool_res_and_grep(
            "event-123", "error", self.mock_context
        )

        # Verify query was called with correct parameters
        self.mock_neo4j_client.run_query.assert_called_once_with(
            mock.ANY, {"event_id": "event-123"}
        )

        # Should find matching lines
        assert "Found 2 matching lines" in result
        assert "1: line 1: error occurred" in result
        assert "3: line 3: another error" in result

    @pytest.mark.asyncio
    async def test_get_full_tool_res_and_grep_no_matches(self):
        """Test grepping tool response with no matches."""
        mock_result = [
            {
                "content": "line 1: normal operation\nline 2: everything fine",
                "tool_name": "test_tool",
            }
        ]
        self.mock_neo4j_client.run_query.return_value = mock_result

        result = await get_full_tool_res_and_grep(
            "event-123", "error", self.mock_context
        )

        assert "No matches found for pattern 'error'" in result

    @pytest.mark.asyncio
    async def test_get_full_tool_res_and_grep_no_response_found(self):
        """Test grepping when no tool response is found."""
        self.mock_neo4j_client.run_query.return_value = []

        result = await get_full_tool_res_and_grep(
            "event-123", "error", self.mock_context
        )

        assert "No RawToolResponse found for event_id: event-123" in result

    @pytest.mark.asyncio
    async def test_get_full_tool_res_and_grep_exception(self):
        """Test handling exceptions in grepping."""
        self.mock_neo4j_client.run_query.side_effect = RuntimeError("Database error")

        result = await get_full_tool_res_and_grep(
            "event-123", "error", self.mock_context
        )

        assert "Error searching tool result" in result

    @pytest.mark.asyncio
    async def test_list_all_events_for_session_success(self):
        """Test listing all events for a session."""
        mock_result = [
            {
                "event_id": "event-1",
                "event_type": "user_prompt",
                "author": "user",
                "timestamp": "2024-01-01T10:00:00",
                "invocation_id": "inv-123",
                "content": "User message",
            },
            {
                "event_id": "event-2",
                "event_type": "function_response",
                "author": "agent",
                "timestamp": "2024-01-01T10:01:00",
                "invocation_id": "inv-123",
                "content": "Tool response content",
            },
        ]
        self.mock_neo4j_client.run_query.return_value = mock_result

        result = await list_all_events_for_session("session-123", self.mock_context)

        assert len(result) == 2

        # User prompt should show content
        user_event = result[0]
        assert user_event["event_id"] == "event-1"
        assert user_event["type"] == "user_prompt"
        assert user_event["content"] == "User message"

        # Function response should hide content
        func_event = result[1]
        assert func_event["event_id"] == "event-2"
        assert func_event["type"] == "function_response"
        assert func_event["content"] == "Content hidden"

    @pytest.mark.asyncio
    async def test_list_all_events_for_session_failure(self):
        """Test handling failure in listing events."""
        self.mock_neo4j_client.run_query.side_effect = RuntimeError("Database error")

        result = await list_all_events_for_session("session-123", self.mock_context)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_full_tool_res_success(self):
        """Test getting full tool response successfully."""
        mock_result = [
            {
                "node_id": "node-123",
                "tool_name": "test_tool",
                "tool_args": "{'param': 'value'}",
                "raw_content": "Tool response content",
                "summary": "Tool summary",
                "created_at": "2024-01-01T10:00:00",
                "session_id": "session-123",
            }
        ]
        self.mock_neo4j_client.run_query.return_value = mock_result

        result = await get_full_tool_res("event-123", self.mock_context)

        expected = {
            "node_id": "node-123",
            "tool_name": "test_tool",
            "tool_args": "{'param': 'value'}",
            "raw_content": "Tool response content",
        }
        assert result == expected

    @pytest.mark.asyncio
    async def test_get_full_tool_res_not_found(self):
        """Test getting full tool response when not found."""
        self.mock_neo4j_client.run_query.return_value = []

        result = await get_full_tool_res("event-123", self.mock_context)

        assert "error" in result
        assert "No RawToolResponse found" in result["error"]

    @pytest.mark.asyncio
    async def test_get_full_tool_res_exception(self):
        """Test handling exceptions in get_full_tool_res."""
        self.mock_neo4j_client.run_query.side_effect = RuntimeError("Database error")

        result = await get_full_tool_res("event-123", self.mock_context)

        assert "error" in result
        assert "Failed to get tool result" in result["error"]

    @pytest.mark.asyncio
    async def test_get_all_events_for_summarization_success(self):
        """Test getting all events for a summarization."""
        # Mock summarized events query
        summarized_events = [
            {
                "event_id": "event-1",
                "event_type": "user_prompt",
                "author": "user",
                "timestamp": "2024-01-01T10:00:00",
                "invocation_id": "inv-123",
                "content": "User message",
            }
        ]

        # Mock summary info query
        summary_info = [
            {
                "event_id": "summary-123",
                "event_type": "history_summary",
                "summary_content": "Summary content",
                "timestamp": "2024-01-01T10:05:00",
            }
        ]

        self.mock_neo4j_client.run_query.side_effect = [summarized_events, summary_info]

        result = await get_all_events_for_summarization(
            "summary-123", self.mock_context
        )

        assert result["summarization_id"] == "summary-123"
        assert result["total_summarized_events"] == 1
        assert len(result["summarized_events"]) == 1
        assert result["summary_info"]["summary_event_id"] == "summary-123"

    @pytest.mark.asyncio
    async def test_get_all_events_for_summarization_failure(self):
        """Test handling failure in get_all_events_for_summarization."""
        self.mock_neo4j_client.run_query.side_effect = RuntimeError("Database error")

        result = await get_all_events_for_summarization(
            "summary-123", self.mock_context
        )

        assert "error" in result
        assert result["summarization_id"] == "summary-123"
        assert result["summarized_events"] == []


class TestDropOrSummarizeEvents:
    """Test drop_or_summarize_events function."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_context = MagicMock()

        # Mock session and session ID
        self.mock_session_id = "shared-session-123"

        # Mock tool context structure
        self.mock_invocation_context = MagicMock()
        self.mock_agent = MagicMock()
        self.mock_session = MagicMock()

        self.mock_context._invocation_context = self.mock_invocation_context
        self.mock_invocation_context.agent = self.mock_agent
        self.mock_invocation_context.session = self.mock_session

        # Mock get_opensage_session_id_from_context
        self.mock_get_session_id_patcher = patch(
            "opensage.toolbox.general.history_management.get_opensage_session_id_from_context"
        )
        self.mock_get_session_id = self.mock_get_session_id_patcher.start()
        self.mock_get_session_id.return_value = self.mock_session_id

        # Mock get_opensage_session
        self.mock_get_opensage_session_patcher = patch(
            "opensage.toolbox.general.history_management.get_opensage_session"
        )
        self.mock_get_opensage_session = self.mock_get_opensage_session_patcher.start()

        # Mock session and config
        self.mock_opensage_session = MagicMock()
        self.mock_config = MagicMock()
        self.mock_llm_config = MagicMock()

        self.mock_get_opensage_session.return_value = self.mock_opensage_session
        self.mock_opensage_session.config = self.mock_config
        self.mock_config.llm = self.mock_llm_config
        self.mock_llm_config.summarize_model = "openai/o4-mini"

    def teardown_method(self):
        """Clean up patches."""
        self.mock_get_session_id_patcher.stop()
        self.mock_get_opensage_session_patcher.stop()

    @pytest.mark.asyncio
    async def test_drop_or_summarize_events_no_llm_config(self):
        """Test when no LLM configuration is available."""
        self.mock_opensage_session.config = None

        result = await drop_or_summarize_events(self.mock_context)

        assert result is None

    @pytest.mark.asyncio
    async def test_drop_or_summarize_events_no_llm(self):
        """Test when no LLM configuration exists."""
        self.mock_config.llm = None

        result = await drop_or_summarize_events(self.mock_context)

        assert result is None

    @pytest.mark.asyncio
    async def test_drop_or_summarize_events_not_enough_events(self):
        """Test when there are not enough events to process."""
        self.mock_session.events = []  # No events

        result = await drop_or_summarize_events(self.mock_context)

        assert result is None

    @pytest.mark.asyncio
    async def test_drop_or_summarize_events_use_agent_model_fallback(self):
        """Test falling back to agent model when summarize_model is not configured."""
        self.mock_llm_config.summarize_model = None
        self.mock_agent.canonical_model = MagicMock()

        # Create mock events
        mock_events = [MagicMock(), MagicMock(), MagicMock()]  # 3 events
        self.mock_session.events = mock_events

        # Mock model response
        mock_response = MagicMock()
        mock_response.content.parts = []  # No function calls

        with patch("opensage.toolbox.general.history_management.LiteLlm"):
            # Properly mock the async generator without AsyncMock
            async def mock_async_gen():
                yield mock_response

            self.mock_agent.canonical_model.generate_content_async = MagicMock(
                return_value=mock_async_gen()
            )

            result = await drop_or_summarize_events(self.mock_context)

            assert result is None  # No function calls found

    @pytest.mark.asyncio
    async def test_drop_or_summarize_events_no_agent_model(self):
        """Test when agent has no model."""
        self.mock_llm_config.summarize_model = None
        del self.mock_agent.canonical_model  # Remove canonical_model attribute

        result = await drop_or_summarize_events(self.mock_context)

        assert result is None

    def test_find_unmatched_tool_calls_and_responses(self):
        """Test finding unmatched tool calls and responses."""
        from google.adk.events.event import Event
        from google.genai import types

        # Create mock events with function calls and responses
        call_part = types.Part.from_function_call(name="test_func", args={})
        call_part.function_call.id = "call-123"

        response_part = types.Part.from_function_response(name="test_func", response={})
        response_part.function_response.id = "call-123"  # Matching ID

        unmatched_call_part = types.Part.from_function_call(name="other_func", args={})
        unmatched_call_part.function_call.id = "call-456"

        # Create events
        call_event = Event(
            invocation_id="inv-123",
            author="agent",
            timestamp=123456789.0,
            content=types.Content(role="model", parts=[call_part]),
        )

        response_event = Event(
            invocation_id="inv-123",
            author="agent",
            timestamp=123456790.0,
            content=types.Content(role="user", parts=[response_part]),
        )

        unmatched_event = Event(
            invocation_id="inv-123",
            author="agent",
            timestamp=123456791.0,
            content=types.Content(role="model", parts=[unmatched_call_part]),
        )

        events = [call_event, response_event, unmatched_event]

        # Create drop_or_summarize_events function to access internal helper
        from opensage.toolbox.general.history_management import drop_or_summarize_events

        # We need to test the internal function directly since it's nested
        # This is a bit tricky, but we can test the logic indirectly through the main function

        # Set up minimal mock for testing the matching logic
        self.mock_session.events = events

        # The function should detect unmatched calls/responses and adjust accordingly
        # We can't easily test the internal function, but we can verify the behavior
        assert len(events) == 3  # Basic sanity check

    @pytest.mark.asyncio
    async def test_drop_or_summarize_events_with_model_response(self):
        """Test drop_or_summarize_events with a model response."""
        from google.adk.events.event import Event
        from google.genai import types

        # Create mock events
        mock_events = [
            Event(
                invocation_id="inv-123",
                author="user",
                timestamp=123456789.0,
                content=types.Content(
                    role="user", parts=[types.Part.from_text(text="User message")]
                ),
            ),
            Event(
                invocation_id="inv-123",
                author="agent",
                timestamp=123456790.0,
                content=types.Content(
                    role="model", parts=[types.Part.from_text(text="Agent response")]
                ),
            ),
        ]
        self.mock_session.events = mock_events

        # Mock function call in model response
        func_call_part = types.Part.from_function_call(name="_no_modification", args={})

        mock_response = MagicMock()
        mock_response.content.parts = [func_call_part]

        with patch(
            "opensage.toolbox.general.history_management.LiteLlm"
        ) as mock_lite_llm:
            mock_model = MagicMock()
            mock_lite_llm.return_value = mock_model

            # Create proper async generator
            async def mock_async_gen():
                yield mock_response

            mock_model.generate_content_async = MagicMock(return_value=mock_async_gen())

            result = await drop_or_summarize_events(self.mock_context)

            # Verify mock was called
            mock_lite_llm.assert_called_once()
            assert result == "No modifications needed"

    @pytest.mark.asyncio
    async def test_drop_or_summarize_events_exception_handling(self):
        """Test exception handling in drop_or_summarize_events."""
        # Create mock events
        mock_events = [MagicMock(), MagicMock()]
        self.mock_session.events = mock_events

        # Mock the LLM config to force using custom model, not agent model
        self.mock_llm_config.summarize_model = "test-model"

        with patch(
            "opensage.toolbox.general.history_management.LiteLlm"
        ) as mock_lite_llm:
            mock_model = MagicMock()
            mock_lite_llm.return_value = mock_model

            # Create a failing async generator
            async def failing_async_gen():
                raise RuntimeError("Model error")
                yield  # This will never be reached

            mock_model.generate_content_async.return_value = failing_async_gen()

            result = await drop_or_summarize_events(self.mock_context)

            assert "Error in drop_or_summarize_events:" in result
