"""Unit tests for neo4j_history_management module."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.adk.events.event import Event
from google.genai import types

from opensage.utils.neo4j_history_management import (
    _create_event_node,
    _create_summarize_relation,
    _determine_event_type,
    _event_exists,
    _extract_event_content,
    _maybe_create_summarize_relation,
    create_agent_call_relation,
    create_history_summary_node,
    create_raw_tool_response_node,
    find_agent_run_by_session_id,
    log_single_event_neo4j,
    record_agent_end,
    record_agent_start,
    store_session_state,
)


class TestEventUtilities:
    """Test utility functions for event processing."""

    def test_determine_event_type_function_call(self):
        """Test determining event type for function calls."""
        # Create event with function call
        func_call_part = types.Part.from_function_call(name="test_func", args={})
        content = types.Content(role="model", parts=[func_call_part])
        event = Event(
            invocation_id="inv-123",
            author="agent",
            timestamp=123456789.0,
            content=content,
        )

        result = _determine_event_type(event)
        assert result == "function_call"

    def test_determine_event_type_function_response(self):
        """Test determining event type for function responses."""
        # Create event with function response
        func_response_part = types.Part.from_function_response(
            name="test_func", response={}
        )
        content = types.Content(role="user", parts=[func_response_part])
        event = Event(
            invocation_id="inv-123",
            author="agent",
            timestamp=123456789.0,
            content=content,
        )

        result = _determine_event_type(event)
        assert result == "function_response"

    def test_determine_event_type_user_prompt(self):
        """Test determining event type for user prompts."""
        # Create user event with text
        text_part = types.Part.from_text(text="User message")
        content = types.Content(role="user", parts=[text_part])
        event = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=content,
        )

        result = _determine_event_type(event)
        assert result == "user_prompt"

    def test_determine_event_type_model_response(self):
        """Test determining event type for model responses."""
        # Create model event with text
        text_part = types.Part.from_text(text="Model response")
        content = types.Content(role="model", parts=[text_part])
        event = Event(
            invocation_id="inv-123",
            author="agent",
            timestamp=123456789.0,
            content=content,
        )

        result = _determine_event_type(event)
        assert result == "model_response"

    def test_determine_event_type_no_content(self):
        """Test determining event type for events with no content."""
        event = Event(
            invocation_id="inv-123", author="agent", timestamp=123456789.0, content=None
        )

        result = _determine_event_type(event)
        assert result == "unknown"

    def test_extract_event_content_text(self):
        """Test extracting content from event with text parts."""
        text_part = types.Part.from_text(text="Test message")
        content = types.Content(role="user", parts=[text_part])
        event = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=content,
        )

        result = _extract_event_content(event)
        parsed = json.loads(result)

        assert len(parsed) == 1
        assert parsed[0]["text"] == "Test message"

    def test_extract_event_content_function_call(self):
        """Test extracting content from event with function call."""
        func_call_part = types.Part.from_function_call(
            name="test_func", args={"param": "value"}
        )
        content = types.Content(role="model", parts=[func_call_part])
        event = Event(
            invocation_id="inv-123",
            author="agent",
            timestamp=123456789.0,
            content=content,
        )

        result = _extract_event_content(event)
        parsed = json.loads(result)

        assert len(parsed) == 1
        assert parsed[0]["function_call"]["name"] == "test_func"
        assert parsed[0]["function_call"]["args"] == {"param": "value"}

    def test_extract_event_content_function_response(self):
        """Test extracting content from event with function response."""
        func_response_part = types.Part.from_function_response(
            name="test_func", response={"result": "success"}
        )
        content = types.Content(role="user", parts=[func_response_part])
        event = Event(
            invocation_id="inv-123",
            author="agent",
            timestamp=123456789.0,
            content=content,
        )

        result = _extract_event_content(event)
        parsed = json.loads(result)

        assert len(parsed) == 1
        assert parsed[0]["function_response"]["name"] == "test_func"
        assert parsed[0]["function_response"]["response"] == {"result": "success"}

    def test_extract_event_content_no_content(self):
        """Test extracting content from event with no content."""
        event = Event(
            invocation_id="inv-123", author="agent", timestamp=123456789.0, content=None
        )

        result = _extract_event_content(event)
        assert result == "[]"

    def test_extract_event_content_empty_parts(self):
        """Test extracting content from event with empty parts."""
        content = types.Content(role="user", parts=[])
        event = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=content,
        )

        result = _extract_event_content(event)
        assert result == "[]"


class TestNeo4jOperations:
    """Test Neo4j database operations."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_context = MagicMock()
        self.mock_tool_context = MagicMock()
        self.mock_neo4j_client = AsyncMock()

        # Mock get_neo4j_client_from_context
        self.mock_get_client_patcher = patch(
            "opensage.utils.neo4j_history_management.get_neo4j_client_from_context"
        )
        self.mock_get_client = self.mock_get_client_patcher.start()
        self.mock_get_client.return_value = self.mock_neo4j_client

        # Mock get_opensage_session_id_from_context
        self.mock_get_session_id_patcher = patch(
            "opensage.utils.neo4j_history_management.get_opensage_session_id_from_context"
        )
        self.mock_get_session_id = self.mock_get_session_id_patcher.start()
        self.mock_get_session_id.return_value = "shared-session-123"

    def teardown_method(self):
        """Clean up patches."""
        self.mock_get_client_patcher.stop()
        self.mock_get_session_id_patcher.stop()

    @pytest.mark.asyncio
    async def test_record_agent_start_success(self):
        """Test successful agent start recording."""
        # Mock agent
        mock_agent = MagicMock()
        mock_agent.name = "test-agent"
        mock_agent.model.model = "test-model"

        # Mock invocation context
        mock_context = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "session-123"
        mock_session.events = []
        mock_context.session = mock_session

        # Mock user content
        mock_user_content = MagicMock()
        mock_text_part = types.Part.from_text(text="User input")
        mock_user_content.parts = [mock_text_part]
        mock_context.user_content = mock_user_content

        session_id = await record_agent_start(mock_agent, mock_context)

        # Verify query was called
        self.mock_neo4j_client.run_query.assert_called()
        call_args = self.mock_neo4j_client.run_query.call_args

        # Check query parameters (should be second positional argument)
        query_params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
        assert query_params["session_id"] == "session-123"
        assert query_params["agent_name"] == "test-agent"
        assert query_params["agent_model"] == "test-model"
        assert query_params["input_content"] == "User input"

        assert session_id == "session-123"

    @pytest.mark.asyncio
    async def test_record_agent_start_no_user_content(self):
        """Test agent start recording with no user content."""
        mock_agent = MagicMock()
        mock_agent.name = "test-agent"
        mock_agent.model.model = "test-model"

        mock_context = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "session-123"
        mock_session.events = []
        mock_context.session = mock_session
        mock_context.user_content = None

        session_id = await record_agent_start(mock_agent, mock_context)

        call_args = self.mock_neo4j_client.run_query.call_args
        query_params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
        assert query_params["input_content"] == ""

    @pytest.mark.asyncio
    async def test_record_agent_start_exception_handling(self):
        """Test exception handling in record_agent_start."""
        mock_agent = MagicMock()
        mock_agent.name = "test-agent"

        mock_context = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "session-123"
        mock_context.session = mock_session

        self.mock_neo4j_client.run_query.side_effect = RuntimeError("Database error")

        # Should not raise exception
        session_id = await record_agent_start(mock_agent, mock_context)
        assert session_id == "session-123"

    @pytest.mark.asyncio
    async def test_record_agent_end_success(self):
        """Test successful agent end recording."""
        mock_context = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "session-123"
        mock_context.session = mock_session

        await record_agent_end(mock_context, "Agent output", "completed")

        self.mock_neo4j_client.run_query.assert_called_once()
        call_args = self.mock_neo4j_client.run_query.call_args
        query_params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]

        assert query_params["session_id"] == "session-123"
        assert query_params["output_content"] == "Agent output"
        assert query_params["status"] == "completed"

    @pytest.mark.asyncio
    async def test_record_agent_end_exception_handling(self):
        """Test exception handling in record_agent_end."""
        mock_context = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "session-123"
        mock_context.session = mock_session

        self.mock_neo4j_client.run_query.side_effect = RuntimeError("Database error")

        # Should not raise exception
        await record_agent_end(mock_context, "output", "completed")

    @pytest.mark.asyncio
    async def test_create_agent_call_relation_success(self):
        """Test successful creation of agent call relationship."""
        await create_agent_call_relation(
            caller_agent_name="caller",
            callee_agent_name="callee",
            caller_session_id="caller-session",
            callee_session_id="callee-session",
            input_content="input",
            output_content="output",
            caller_agent_model="caller-model",
            callee_agent_model="callee-model",
            context=self.mock_tool_context,
        )

        # Should call run_query twice (create nodes, then create relationship)
        assert self.mock_neo4j_client.run_query.call_count == 2

    @pytest.mark.asyncio
    async def test_create_agent_call_relation_exception_handling(self):
        """Test exception handling in create_agent_call_relation."""
        self.mock_neo4j_client.run_query.side_effect = RuntimeError("Database error")

        # Should not raise exception
        await create_agent_call_relation(
            caller_agent_name="caller",
            callee_agent_name="callee",
            caller_session_id="caller-session",
            callee_session_id="callee-session",
            input_content="input",
            output_content="output",
            caller_agent_model="caller-model",
            callee_agent_model="callee-model",
            context=self.mock_tool_context,
        )

    @pytest.mark.asyncio
    async def test_store_session_state_success(self):
        """Test successful session state storage."""
        mock_context = MagicMock()
        state_dict = {"key1": "value1", "key2": 42}

        await store_session_state("session-123", state_dict, mock_context)

        self.mock_neo4j_client.run_query.assert_called_once()
        call_args = self.mock_neo4j_client.run_query.call_args
        query_params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]

        assert query_params["session_id"] == "session-123"
        assert json.loads(query_params["state_dict"]) == state_dict

    @pytest.mark.asyncio
    async def test_store_session_state_exception_handling(self):
        """Test exception handling in store_session_state."""
        mock_context = MagicMock()
        self.mock_neo4j_client.run_query.side_effect = RuntimeError("Database error")

        # Should not raise exception
        await store_session_state("session-123", {"key": "value"}, mock_context)

    @pytest.mark.asyncio
    async def test_event_exists_true(self):
        """Test checking event existence when event exists."""
        self.mock_neo4j_client.run_query.return_value = [
            {"found_event_id": "event-123"}
        ]

        result = await _event_exists("event-123", "session-123", self.mock_context)

        assert result is True
        self.mock_neo4j_client.run_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_event_exists_false(self):
        """Test checking event existence when event doesn't exist."""
        self.mock_neo4j_client.run_query.return_value = []

        result = await _event_exists("event-123", "session-123", self.mock_context)

        assert result is False

    @pytest.mark.asyncio
    async def test_event_exists_exception_handling(self):
        """Test exception handling in event existence check."""
        self.mock_neo4j_client.run_query.side_effect = RuntimeError("Database error")

        result = await _event_exists("event-123", "session-123", self.mock_context)

        assert result is False

    @pytest.mark.asyncio
    async def test_create_event_node_success(self):
        """Test successful event node creation."""
        # Create test event
        text_part = types.Part.from_text(text="Test message")
        content = types.Content(role="user", parts=[text_part])
        event = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=content,
        )

        self.mock_neo4j_client.run_query.return_value = [{"created_event_id": event.id}]

        await _create_event_node(event, "session-123", self.mock_context)

        self.mock_neo4j_client.run_query.assert_called_once()
        call_args = self.mock_neo4j_client.run_query.call_args
        query_params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]

        assert query_params["event_id"] == event.id
        assert query_params["session_id"] == "session-123"
        assert query_params["author"] == "user"

    @pytest.mark.asyncio
    async def test_create_event_node_serialization_failure(self):
        """Test event node creation with serialization failure."""
        # Create event that might fail serialization
        event = MagicMock()
        event.id = "event-123"
        event.invocation_id = "inv-123"
        event.author = "user"
        event.timestamp = 123456789.0
        event.content = None
        event.model_dump_json.side_effect = RuntimeError("Serialization failed")

        await _create_event_node(event, "session-123", self.mock_context)

        # Should still call run_query with error message in raw_content
        self.mock_neo4j_client.run_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_event_node_exception_handling(self):
        """Test exception handling in event node creation."""
        text_part = types.Part.from_text(text="Test message")
        content = types.Content(role="user", parts=[text_part])
        event = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=content,
        )

        self.mock_neo4j_client.run_query.side_effect = RuntimeError("Database error")

        # Should not raise exception
        await _create_event_node(event, "session-123", self.mock_context)

    @pytest.mark.asyncio
    async def test_create_raw_tool_response_node_success(self):
        """Test successful raw tool response node creation."""
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"

        args = {"param": "value"}
        tool_response = "Tool response content"
        summary = "Tool summary"

        self.mock_tool_context._invocation_context.session.id = "session-123"

        self.mock_neo4j_client.run_query.return_value = [
            {"created_node_id": str(uuid.uuid4())}
        ]

        result = await create_raw_tool_response_node(
            mock_tool, args, self.mock_tool_context, tool_response, summary
        )

        assert result is True
        self.mock_neo4j_client.run_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_raw_tool_response_node_exception_handling(self):
        """Test exception handling in raw tool response node creation."""
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"

        self.mock_tool_context._invocation_context.session.id = "session-123"
        self.mock_neo4j_client.run_query.side_effect = RuntimeError("Database error")

        result = await create_raw_tool_response_node(
            mock_tool, {}, self.mock_tool_context, "response", "summary"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_create_summarize_relation_success(self):
        """Test successful creation of summarize relation."""
        text_part = types.Part.from_text(text="Test message")
        content = types.Content(role="user", parts=[text_part])
        event = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=content,
        )

        self.mock_neo4j_client.run_query.return_value = [
            {"matched_node_id": "node-123", "tool_name": "test_tool"}
        ]

        result = await _create_summarize_relation(
            event, "session-123", self.mock_context, "summary content"
        )

        assert result is True
        self.mock_neo4j_client.run_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_summarize_relation_no_match(self):
        """Test create summarize relation when no match found."""
        text_part = types.Part.from_text(text="Test message")
        content = types.Content(role="user", parts=[text_part])
        event = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=content,
        )

        self.mock_neo4j_client.run_query.return_value = []

        result = await _create_summarize_relation(
            event, "session-123", self.mock_context, "summary content"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_maybe_create_summarize_relation_with_summary(self):
        """Test maybe create summarize relation with summary tags in text."""
        summary_text = "<Summary by opensage>This is a summary</Summary by opensage>"
        text_part = types.Part.from_text(text=summary_text)
        content = types.Content(role="user", parts=[text_part])
        event = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=content,
        )

        with patch(
            "opensage.utils.neo4j_history_management._create_summarize_relation"
        ) as mock_create_relation:
            mock_create_relation.return_value = True

            result = await _maybe_create_summarize_relation(
                event, "session-123", self.mock_context
            )

            assert result is True
            mock_create_relation.assert_called_once_with(
                event, "session-123", self.mock_context, summary_text
            )

    @pytest.mark.asyncio
    async def test_maybe_create_summarize_relation_in_function_response(self):
        """Test maybe create summarize relation with summary in function response."""
        summary_content = "<Summary by opensage>Function summary</Summary by opensage>"
        func_response_part = types.Part.from_function_response(
            name="test_func", response={"result": summary_content}
        )
        content = types.Content(role="user", parts=[func_response_part])
        event = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=content,
        )

        with patch(
            "opensage.utils.neo4j_history_management._create_summarize_relation"
        ) as mock_create_relation:
            mock_create_relation.return_value = True

            result = await _maybe_create_summarize_relation(
                event, "session-123", self.mock_context
            )

            assert result is True
            mock_create_relation.assert_called_once_with(
                event, "session-123", self.mock_context, summary_content
            )

    @pytest.mark.asyncio
    async def test_maybe_create_summarize_relation_no_summary(self):
        """Test maybe create summarize relation with no summary tags."""
        text_part = types.Part.from_text(text="Regular message without summary")
        content = types.Content(role="user", parts=[text_part])
        event = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=content,
        )

        result = await _maybe_create_summarize_relation(
            event, "session-123", self.mock_context
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_log_single_event_neo4j_new_event(self):
        """Test logging a single new event."""
        text_part = types.Part.from_text(text="Test message")
        content = types.Content(role="user", parts=[text_part])
        event = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=content,
        )

        with (
            patch(
                "opensage.utils.neo4j_history_management._event_exists"
            ) as mock_exists,
            patch(
                "opensage.utils.neo4j_history_management._create_event_node"
            ) as mock_create,
            patch(
                "opensage.utils.neo4j_history_management._maybe_create_summarize_relation"
            ) as mock_summarize,
        ):
            mock_exists.return_value = False  # Event doesn't exist
            mock_create.return_value = None
            mock_summarize.return_value = False

            await log_single_event_neo4j(event, "session-123", self.mock_context)

            mock_exists.assert_called_once()
            mock_create.assert_called_once()
            mock_summarize.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_single_event_neo4j_existing_event(self):
        """Test logging an existing event (should skip)."""
        text_part = types.Part.from_text(text="Test message")
        content = types.Content(role="user", parts=[text_part])
        event = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=content,
        )

        with (
            patch(
                "opensage.utils.neo4j_history_management._event_exists"
            ) as mock_exists,
            patch(
                "opensage.utils.neo4j_history_management._create_event_node"
            ) as mock_create,
        ):
            mock_exists.return_value = True  # Event already exists

            await log_single_event_neo4j(event, "session-123", self.mock_context)

            mock_exists.assert_called_once()
            mock_create.assert_not_called()  # Should skip creation

    @pytest.mark.asyncio
    async def test_find_agent_run_by_session_id_found(self):
        """Test finding agent run by session ID when it exists."""
        self.mock_neo4j_client.run_query.return_value = [
            {"found_session_id": "session-123", "agent_name": "test-agent"}
        ]

        result = await find_agent_run_by_session_id("session-123", self.mock_context)

        assert result == "session-123"
        self.mock_neo4j_client.run_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_find_agent_run_by_session_id_not_found(self):
        """Test finding agent run by session ID when it doesn't exist."""
        self.mock_neo4j_client.run_query.return_value = []

        result = await find_agent_run_by_session_id("session-123", self.mock_context)

        assert result is None

    @pytest.mark.asyncio
    async def test_find_agent_run_by_session_id_exception_handling(self):
        """Test exception handling in find_agent_run_by_session_id."""
        self.mock_neo4j_client.run_query.side_effect = RuntimeError("Database error")

        result = await find_agent_run_by_session_id("session-123", self.mock_context)

        assert result is None

    @pytest.mark.asyncio
    async def test_create_history_summary_node_success(self):
        """Test successful creation of history summary node."""
        # Create summary event
        summary_text = "This is a history summary"
        text_part = types.Part.from_text(text=summary_text)
        content = types.Content(role="user", parts=[text_part])
        summary_event = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=content,
        )

        # Create events to summarize
        event1 = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456780.0,
            content=types.Content(
                role="user", parts=[types.Part.from_text(text="Event 1")]
            ),
        )
        event2 = Event(
            invocation_id="inv-123",
            author="agent",
            timestamp=123456785.0,
            content=types.Content(
                role="model", parts=[types.Part.from_text(text="Event 2")]
            ),
        )

        events_to_summarize = [event1, event2]

        self.mock_tool_context._invocation_context.session.id = "session-123"

        # Mock successful query responses
        self.mock_neo4j_client.run_query.return_value = [{"event_id": summary_event.id}]

        result = await create_history_summary_node(
            self.mock_tool_context, summary_event, events_to_summarize
        )

        assert result is True
        # Should call run_query multiple times (create summary + remove relations + create new relations)
        assert self.mock_neo4j_client.run_query.call_count >= 3

    @pytest.mark.asyncio
    async def test_create_history_summary_node_exception_handling(self):
        """Test exception handling in create_history_summary_node."""
        summary_event = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=types.Content(
                role="user", parts=[types.Part.from_text(text="Summary")]
            ),
        )

        self.mock_tool_context._invocation_context.session.id = "session-123"
        self.mock_neo4j_client.run_query.side_effect = RuntimeError("Database error")

        result = await create_history_summary_node(
            self.mock_tool_context, summary_event, []
        )

        assert result is False
