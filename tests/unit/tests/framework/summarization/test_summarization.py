"""Unit tests for summarization module."""

from __future__ import annotations

from datetime import datetime
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.adk.events.event import Event
from google.genai import types

from aigise.features.summarization import (
    _get_summary_async,
    _summarize_events_async,
    history_summarizer_callback,
    setup_summarization_callbacks,
    tool_response_summarizer_callback,
)


class TestSummarizationHelpers:
    """Test helper functions for summarization."""

    @pytest.mark.asyncio
    async def test_get_summary_async_single_response(self):
        """Test getting summary from model with single response."""
        mock_model = MagicMock()
        mock_llm_request = MagicMock()

        # Mock model response
        mock_response = MagicMock()
        mock_response.content.parts = [types.Part.from_text(text="Generated summary")]

        async def mock_async_gen():
            yield mock_response

        mock_model.generate_content_async.return_value = mock_async_gen()

        result = await _get_summary_async(mock_model, mock_llm_request)

        assert result == "Generated summary"

    @pytest.mark.asyncio
    async def test_get_summary_async_multiple_parts(self):
        """Test getting summary from model with multiple text parts."""
        mock_model = MagicMock()
        mock_llm_request = MagicMock()

        # Mock model response with multiple parts
        mock_response = MagicMock()
        part1 = types.Part.from_text(text="Part 1 ")
        part2 = types.Part.from_text(text="Part 2")
        mock_response.content.parts = [part1, part2]

        async def mock_async_gen():
            yield mock_response

        mock_model.generate_content_async.return_value = mock_async_gen()

        result = await _get_summary_async(mock_model, mock_llm_request)

        assert result == "Part 1 Part 2"

    @pytest.mark.asyncio
    async def test_get_summary_async_multiple_responses(self):
        """Test getting summary from model with multiple responses."""
        mock_model = MagicMock()
        mock_llm_request = MagicMock()

        # Mock multiple responses
        response1 = MagicMock()
        response1.content.parts = [types.Part.from_text(text="First ")]

        response2 = MagicMock()
        response2.content.parts = [types.Part.from_text(text="Second")]

        async def mock_async_gen():
            yield response1
            yield response2

        mock_model.generate_content_async.return_value = mock_async_gen()

        result = await _get_summary_async(mock_model, mock_llm_request)

        assert result == "First Second"

    @pytest.mark.asyncio
    async def test_get_summary_async_empty_response(self):
        """Test getting summary from model with empty response."""
        mock_model = MagicMock()
        mock_llm_request = MagicMock()

        # Mock empty response
        mock_response = MagicMock()
        mock_response.content.parts = []

        async def mock_async_gen():
            yield mock_response

        mock_model.generate_content_async.return_value = mock_async_gen()

        result = await _get_summary_async(mock_model, mock_llm_request)

        assert result == ""

    @pytest.mark.asyncio
    async def test_summarize_events_async(self):
        """Test summarizing events with LLM."""
        # Create test events
        event1 = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=types.Content(
                role="user", parts=[types.Part.from_text(text="User message")]
            ),
        )

        func_call_part = types.Part.from_function_call(
            name="test_func", args={"param": "value"}
        )
        event2 = Event(
            invocation_id="inv-123",
            author="agent",
            timestamp=123456790.0,
            content=types.Content(role="model", parts=[func_call_part]),
        )

        func_response_part = types.Part.from_function_response(
            name="test_func", response={"result": "success"}
        )
        event3 = Event(
            invocation_id="inv-123",
            author="agent",
            timestamp=123456791.0,
            content=types.Content(role="user", parts=[func_response_part]),
        )

        events_to_summarize = [event1, event2, event3]

        mock_model = MagicMock()
        mock_response = MagicMock(
            content=MagicMock(parts=[types.Part.from_text(text="Events summary")])
        )

        async def mock_async_gen():
            yield mock_response

        mock_model.generate_content_async.return_value = mock_async_gen()

        result = await _summarize_events_async(mock_model, events_to_summarize)

        assert result == "Events summary"
        mock_model.generate_content_async.assert_called_once()


class TestToolResponseSummarizer:
    """Test tool response summarization functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_tool = MagicMock()
        self.mock_tool.name = "test_tool"

        self.mock_args = {"param": "value"}

        self.mock_tool_context = MagicMock()
        self.mock_invocation_context = MagicMock()
        self.mock_agent = MagicMock()

        self.mock_tool_context._invocation_context = self.mock_invocation_context
        self.mock_invocation_context.agent = self.mock_agent

        # Mock session and config
        self.mock_session_id = "shared-session-123"

        self.mock_get_session_id_patcher = patch(
            "aigise.features.summarization.get_aigise_session_id_from_context"
        )
        self.mock_get_session_id = self.mock_get_session_id_patcher.start()
        self.mock_get_session_id.return_value = self.mock_session_id

        self.mock_get_aigise_session_patcher = patch(
            "aigise.session.get_aigise_session"
        )
        self.mock_get_aigise_session = self.mock_get_aigise_session_patcher.start()

        self.mock_aigise_session = MagicMock()
        self.mock_config = MagicMock()
        self.mock_history_config = MagicMock()
        self.mock_llm_config = MagicMock()

        self.mock_get_aigise_session.return_value = self.mock_aigise_session
        self.mock_aigise_session.config = self.mock_config
        self.mock_config.history = self.mock_history_config
        self.mock_config.llm = self.mock_llm_config

        # Mock neo4j logging
        self.mock_neo4j_logging_patcher = patch(
            "aigise.features.summarization.is_neo4j_logging_enabled"
        )
        self.mock_neo4j_logging = self.mock_neo4j_logging_patcher.start()
        self.mock_neo4j_logging.return_value = False  # Default to disabled

    def teardown_method(self):
        """Clean up patches."""
        self.mock_get_session_id_patcher.stop()
        self.mock_get_aigise_session_patcher.stop()
        self.mock_neo4j_logging_patcher.stop()

    @pytest.mark.asyncio
    async def test_tool_response_summarizer_callback_short_response(self):
        """Test tool response summarizer with short response (no summarization needed)."""
        self.mock_history_config.max_tool_response_length = 1000
        tool_response = "Short response"  # Less than 1000 chars

        result = await tool_response_summarizer_callback(
            self.mock_tool, self.mock_args, self.mock_tool_context, tool_response
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_tool_response_summarizer_callback_long_response(self):
        """Test tool response summarizer with long response (needs summarization)."""
        self.mock_history_config.max_tool_response_length = 100
        self.mock_llm_config.summarize_model = "openai/gpt-3.5-turbo"

        # Long response that exceeds threshold
        tool_response = "x" * 200

        with patch("aigise.features.summarization.LiteLlm") as mock_lite_llm:
            mock_model = MagicMock()
            mock_lite_llm.return_value = mock_model

            # Mock model response
            mock_response = MagicMock()
            mock_response.content.parts = [
                types.Part.from_text(text="Generated summary")
            ]

            async def mock_async_gen():
                yield mock_response

            mock_model.generate_content_async.return_value = mock_async_gen()

            result = await tool_response_summarizer_callback(
                self.mock_tool, self.mock_args, self.mock_tool_context, tool_response
            )

            # Verify mock was called
            mock_lite_llm.assert_called_once_with(model="openai/gpt-3.5-turbo")
            mock_model.generate_content_async.assert_called_once()

            assert result.startswith("<Summary by aigise>")
            assert result.endswith("</Summary by aigise>")
            # Should contain the mocked summary text
            assert "Generated summary" in result

    @pytest.mark.asyncio
    async def test_tool_response_summarizer_callback_no_model_config(self):
        """Test tool response summarizer with no model configuration (fallback to agent model)."""
        self.mock_history_config.max_tool_response_length = 10
        self.mock_llm_config.summarize_model = None
        self.mock_agent.canonical_model = MagicMock()

        tool_response = "x" * 50  # Long response

        # Mock agent model response
        mock_response = MagicMock()
        mock_response.content.parts = [types.Part.from_text(text="Agent model summary")]

        async def mock_async_gen():
            yield mock_response

        self.mock_agent.canonical_model.generate_content_async.return_value = (
            mock_async_gen()
        )

        result = await tool_response_summarizer_callback(
            self.mock_tool, self.mock_args, self.mock_tool_context, tool_response
        )

        assert "Agent model summary" in result

    @pytest.mark.asyncio
    async def test_tool_response_summarizer_callback_no_agent_model(self):
        """Test tool response summarizer with no agent model available."""
        self.mock_history_config.max_tool_response_length = 10
        self.mock_llm_config.summarize_model = None
        # Remove canonical_model attribute from agent
        del self.mock_agent.canonical_model

        tool_response = "x" * 50  # Long response

        result = await tool_response_summarizer_callback(
            self.mock_tool, self.mock_args, self.mock_tool_context, tool_response
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_tool_response_summarizer_callback_model_error(self):
        """Test tool response summarizer with model error (fallback to truncation)."""
        self.mock_history_config.max_tool_response_length = 10
        self.mock_llm_config.summarize_model = "openai/gpt-3.5-turbo"

        tool_response = "x" * 50  # Long response

        with patch("aigise.features.summarization.LiteLlm") as mock_lite_llm:
            mock_model = MagicMock()
            mock_lite_llm.return_value = mock_model
            mock_model.generate_content_async.side_effect = RuntimeError("Model error")

            result = await tool_response_summarizer_callback(
                self.mock_tool, self.mock_args, self.mock_tool_context, tool_response
            )

            # Verify mock was called (even though it failed)
            mock_lite_llm.assert_called_once_with(model="openai/gpt-3.5-turbo")
            mock_model.generate_content_async.assert_called_once()

            # Should fallback to truncation when model fails
            assert result.startswith("<Summary by aigise>")
            assert result.endswith("</Summary by aigise>")
            # Should contain the truncated original response (all 50 x's since < 1000)
            assert "x" * 50 in result
            # Should NOT contain LLM-generated content since model failed
            assert "Summary:" not in result

    @pytest.mark.asyncio
    async def test_tool_response_summarizer_callback_with_neo4j_logging(self):
        """Test tool response summarizer with Neo4j logging enabled."""
        self.mock_history_config.max_tool_response_length = 10
        self.mock_llm_config.summarize_model = "openai/gpt-3.5-turbo"
        self.mock_neo4j_logging.return_value = True

        tool_response = "x" * 50  # Long response

        with (
            patch("aigise.features.summarization.LiteLlm") as mock_lite_llm,
            patch(
                "aigise.utils.neo4j_history_management.create_raw_tool_response_node"
            ) as mock_create_node,
        ):
            mock_model = MagicMock()
            mock_lite_llm.return_value = mock_model

            mock_response = MagicMock()
            mock_response.content.parts = [types.Part.from_text(text="Summary")]

            async def mock_async_gen():
                yield mock_response

            mock_model.generate_content_async.return_value = mock_async_gen()

            mock_create_node.return_value = None

            result = await tool_response_summarizer_callback(
                self.mock_tool, self.mock_args, self.mock_tool_context, tool_response
            )

            # Verify mocks were called
            mock_lite_llm.assert_called_once_with(model="openai/gpt-3.5-turbo")
            mock_model.generate_content_async.assert_called_once()
            # Verify Neo4j node creation was called
            mock_create_node.assert_called_once()

            # Verify result format and content
            assert result.startswith("<Summary by aigise>")
            assert result.endswith("</Summary by aigise>")
            assert "Summary" in result


class TestHistorySummarizer:
    """Test history summarization functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_tool_context = MagicMock()
        self.mock_invocation_context = MagicMock()
        self.mock_agent = MagicMock()
        self.mock_session = MagicMock()

        self.mock_tool_context._invocation_context = self.mock_invocation_context
        self.mock_invocation_context.agent = self.mock_agent
        self.mock_invocation_context.session = self.mock_session
        self.mock_invocation_context.invocation_id = "inv-123"

        # Mock agent model
        self.mock_agent.canonical_model = MagicMock()

        # Mock session and config
        self.mock_session_id = "shared-session-123"

        self.mock_get_session_id_patcher = patch(
            "aigise.features.summarization.get_aigise_session_id_from_context"
        )
        self.mock_get_session_id = self.mock_get_session_id_patcher.start()
        self.mock_get_session_id.return_value = self.mock_session_id

        self.mock_get_aigise_session_patcher = patch(
            "aigise.session.get_aigise_session"
        )
        self.mock_get_aigise_session = self.mock_get_aigise_session_patcher.start()

        self.mock_aigise_session = MagicMock()
        self.mock_config = MagicMock()
        self.mock_history_config = MagicMock()
        self.mock_llm_config = MagicMock()

        self.mock_get_aigise_session.return_value = self.mock_aigise_session
        self.mock_aigise_session.config = self.mock_config
        self.mock_config.history = self.mock_history_config
        self.mock_config.llm = self.mock_llm_config

        # Set up default config values
        self.mock_history_config.max_history_summary_length = 1000
        self.mock_history_config.max_tool_response_length = 200
        self.mock_llm_config.summarize_model = None  # Use agent model

        # Mock neo4j logging
        self.mock_neo4j_logging_patcher = patch(
            "aigise.features.summarization.is_neo4j_logging_enabled"
        )
        self.mock_neo4j_logging = self.mock_neo4j_logging_patcher.start()
        self.mock_neo4j_logging.return_value = False

    def teardown_method(self):
        """Clean up patches."""
        self.mock_get_session_id_patcher.stop()
        self.mock_get_aigise_session_patcher.stop()
        self.mock_neo4j_logging_patcher.stop()

    @pytest.mark.asyncio
    async def test_history_summarizer_callback_no_agent_model(self):
        """Test history summarizer when agent has no model."""
        del self.mock_agent.canonical_model  # Remove model attribute

        result = await history_summarizer_callback(
            None, None, self.mock_tool_context, None
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_history_summarizer_callback_too_few_events(self):
        """Test history summarizer with too few events."""
        self.mock_session.events = [MagicMock()]  # Only 1 event

        result = await history_summarizer_callback(
            None, None, self.mock_tool_context, None
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_history_summarizer_callback_short_history(self):
        """Test history summarizer with short history (no summarization needed)."""
        # Create short events
        event1 = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=types.Content(
                role="user", parts=[types.Part.from_text(text="Short message")]
            ),
        )
        event2 = Event(
            invocation_id="inv-123",
            author="agent",
            timestamp=123456790.0,
            content=types.Content(
                role="model", parts=[types.Part.from_text(text="Short response")]
            ),
        )

        self.mock_session.events = [event1, event2]

        result = await history_summarizer_callback(
            None, None, self.mock_tool_context, None
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_history_summarizer_callback_long_history(self):
        """Test history summarizer with long history (needs summarization)."""
        # Create long events that exceed threshold
        long_text = "x" * 500  # Long text

        event1 = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=types.Content(
                role="user", parts=[types.Part.from_text(text=long_text)]
            ),
        )
        event2 = Event(
            invocation_id="inv-123",
            author="agent",
            timestamp=123456790.0,
            content=types.Content(
                role="model", parts=[types.Part.from_text(text=long_text)]
            ),
        )
        event3 = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456791.0,
            content=types.Content(
                role="user", parts=[types.Part.from_text(text="Recent message")]
            ),
        )

        self.mock_session.events = [event1, event2, event3]

        # Mock model response
        mock_response = MagicMock()
        mock_response.content.parts = [types.Part.from_text(text="History summary")]

        async def mock_async_gen():
            yield mock_response

        self.mock_agent.canonical_model.generate_content_async.return_value = (
            mock_async_gen()
        )

        result = await history_summarizer_callback(
            None, None, self.mock_tool_context, None
        )

        assert (
            result is None
        )  # Function doesn't return the summary, just modifies session

        # Verify session events were modified
        # Since there are no incomplete tool calls, only the summary event remains
        assert len(self.mock_session.events) == 1  # Only summary event
        assert (
            self.mock_session.events[0].content.parts[0].text
            == "[History Summary] History summary"
        )

    @pytest.mark.asyncio
    async def test_history_summarizer_callback_with_incomplete_tool_calls(self):
        """Test history summarizer preserving incomplete tool calls."""
        long_text = "x" * 500

        # Event that will be summarized
        event1 = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=types.Content(
                role="user", parts=[types.Part.from_text(text=long_text)]
            ),
        )

        # Tool call without response (should be kept)
        func_call_part = types.Part.from_function_call(name="test_func", args={})
        func_call_part.function_call.id = "call-123"

        event2 = Event(
            invocation_id="inv-123",
            author="agent",
            timestamp=123456790.0,
            content=types.Content(role="model", parts=[func_call_part]),
        )

        self.mock_session.events = [event1, event2]

        # Mock model response
        mock_response = MagicMock()
        mock_response.content.parts = [types.Part.from_text(text="Summary")]

        async def mock_async_gen():
            yield mock_response

        self.mock_agent.canonical_model.generate_content_async.return_value = (
            mock_async_gen()
        )

        result = await history_summarizer_callback(
            None, None, self.mock_tool_context, None
        )

        # Should keep the incomplete tool call
        assert len(self.mock_session.events) == 2  # Summary + incomplete tool call

    @pytest.mark.asyncio
    async def test_history_summarizer_callback_with_neo4j_logging(self):
        """Test history summarizer with Neo4j logging enabled."""
        self.mock_neo4j_logging.return_value = True

        # Set lower thresholds to ensure summarization triggers
        # Total text will be 500+500+10=1010, so threshold should be < 1010
        self.mock_history_config.max_history_summary_length = 1000
        self.mock_history_config.max_tool_response_length = 0

        long_text = "x" * 500
        event1 = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=types.Content(
                role="user", parts=[types.Part.from_text(text=long_text)]
            ),
        )
        event2 = Event(
            invocation_id="inv-123",
            author="agent",
            timestamp=123456790.0,
            content=types.Content(
                role="model", parts=[types.Part.from_text(text=long_text)]
            ),
        )
        event3 = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456791.0,
            content=types.Content(
                role="user", parts=[types.Part.from_text(text="Extra text")]
            ),
        )

        self.mock_session.events = [event1, event2, event3]

        # Mock model response
        mock_response = MagicMock()
        mock_response.content.parts = [types.Part.from_text(text="Summary")]

        async def mock_async_gen():
            yield mock_response

        self.mock_agent.canonical_model.generate_content_async.return_value = (
            mock_async_gen()
        )

        with patch(
            "aigise.utils.neo4j_history_management.create_history_summary_node"
        ) as mock_create_node:
            mock_create_node.return_value = None

            result = await history_summarizer_callback(
                None, None, self.mock_tool_context, None
            )

            # Verify Neo4j node creation was called
            mock_create_node.assert_called_once()

    @pytest.mark.asyncio
    async def test_history_summarizer_callback_model_error(self):
        """Test history summarizer with model error."""
        long_text = "x" * 500
        event1 = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=types.Content(
                role="user", parts=[types.Part.from_text(text=long_text)]
            ),
        )
        event2 = Event(
            invocation_id="inv-123",
            author="agent",
            timestamp=123456790.0,
            content=types.Content(
                role="model", parts=[types.Part.from_text(text=long_text)]
            ),
        )

        self.mock_session.events = [event1, event2]

        # Mock model error
        self.mock_agent.canonical_model.generate_content_async.side_effect = (
            RuntimeError("Model error")
        )

        result = await history_summarizer_callback(
            None, None, self.mock_tool_context, None
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_history_summarizer_callback_with_custom_model(self):
        """Test history summarizer with custom summarize model."""
        self.mock_llm_config.summarize_model = "anthropic/claude-3-5-sonnet"

        # Set lower thresholds to ensure summarization triggers
        self.mock_history_config.max_history_summary_length = 1000
        self.mock_history_config.max_tool_response_length = 0

        long_text = "x" * 500
        event1 = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456789.0,
            content=types.Content(
                role="user", parts=[types.Part.from_text(text=long_text)]
            ),
        )
        event2 = Event(
            invocation_id="inv-123",
            author="agent",
            timestamp=123456790.0,
            content=types.Content(
                role="model", parts=[types.Part.from_text(text=long_text)]
            ),
        )
        event3 = Event(
            invocation_id="inv-123",
            author="user",
            timestamp=123456791.0,
            content=types.Content(
                role="user", parts=[types.Part.from_text(text="Extra text")]
            ),
        )

        self.mock_session.events = [event1, event2, event3]

        with patch("aigise.features.summarization.LiteLlm") as mock_lite_llm:
            mock_model = MagicMock()
            mock_lite_llm.return_value = mock_model

            mock_response = MagicMock()
            mock_response.content.parts = [types.Part.from_text(text="Custom summary")]

            async def mock_async_gen():
                yield mock_response

            mock_model.generate_content_async.return_value = mock_async_gen()

            result = await history_summarizer_callback(
                None, None, self.mock_tool_context, None
            )

            # Verify custom model was used
            mock_lite_llm.assert_called_once_with(model="anthropic/claude-3-5-sonnet")


class TestSetupSummarizationCallbacks:
    """Test setup function for summarization callbacks."""

    def test_setup_summarization_callbacks(self):
        """Test setting up summarization callbacks on agents."""
        # Create mock agents
        root_agent = MagicMock()
        sub_agent1 = MagicMock()
        sub_agent2 = MagicMock()

        root_agent.name = "root"
        sub_agent1.name = "sub1"
        sub_agent2.name = "sub2"

        with (
            patch("aigise.features.summarization.discover_all_agents") as mock_discover,
            patch(
                "aigise.features.summarization.register_callback_to_all_agents"
            ) as mock_register,
        ):
            # Mock discovered agents
            all_agents = [root_agent, sub_agent1, sub_agent2]
            mock_discover.return_value = all_agents

            # Mock successful registration
            mock_register.return_value = {
                root_agent: 2,  # 2 callbacks registered
                sub_agent1: 2,
                sub_agent2: 2,
            }

            setup_summarization_callbacks(root_agent)

            # Verify discovery was called
            mock_discover.assert_called_once_with(root_agent)

            # Verify registration was called with correct callbacks
            mock_register.assert_called_once()
            call_args = mock_register.call_args
            assert call_args[0][0] == all_agents  # First arg: agents list
            callbacks = call_args[0][1]  # Second arg: callbacks list
            assert len(callbacks) == 2
            assert history_summarizer_callback in callbacks
            assert tool_response_summarizer_callback in callbacks

    def test_setup_summarization_callbacks_no_agents(self):
        """Test setup with no agents discovered."""
        root_agent = MagicMock()
        root_agent.name = "root"

        with (
            patch("aigise.features.summarization.discover_all_agents") as mock_discover,
            patch(
                "aigise.features.summarization.register_callback_to_all_agents"
            ) as mock_register,
        ):
            # Mock no agents discovered
            mock_discover.return_value = []
            mock_register.return_value = {}

            setup_summarization_callbacks(root_agent)

            mock_discover.assert_called_once_with(root_agent)
            mock_register.assert_called_once_with([], mock.ANY)
