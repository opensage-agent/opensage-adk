"""Unit tests for memory tools module (graph-based memory system)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import from the new memory system
from aigise.memory.tools.memory_search_tools import (
    get_entity_by_id,
    list_memory_contents,
    search_memory,
)
from aigise.memory.tools.memory_update_tools import (
    cache_qa_pair,
    delete_from_memory,
    delete_relationship_from_memory,
    ensure_memory_indexes,
)


class TestSearchMemory:
    """Test memory search functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_tool_context = MagicMock()
        self.mock_neo4j_client = AsyncMock()

        # Mock get_neo4j_client_from_context
        self.mock_get_client_patcher = patch(
            "aigise.memory.tools.memory_search_tools.get_neo4j_client_from_context"
        )
        self.mock_get_client = self.mock_get_client_patcher.start()
        self.mock_get_client.return_value = self.mock_neo4j_client

    def teardown_method(self):
        """Clean up patches."""
        self.mock_get_client_patcher.stop()

    @pytest.mark.asyncio
    async def test_search_memory_success(self):
        """Test successful memory search."""
        # Mock the search controller to return results
        with patch(
            "aigise.memory.tools.memory_search_tools._get_search_controller"
        ) as mock_controller_factory:
            mock_controller = MagicMock()
            mock_controller_factory.return_value = mock_controller

            # Create mock search result
            mock_result = MagicMock()
            mock_result.has_results = True
            mock_result.total_found = 2
            mock_result.strategy_used = "embedding_search"
            mock_result.iterations = 1
            mock_result.items = [
                MagicMock(
                    node_label="Question",
                    score=0.9,
                    match_type="similarity",
                    properties={"text": "Similar question 1"},
                ),
                MagicMock(
                    node_label="Question",
                    score=0.8,
                    match_type="similarity",
                    properties={"text": "Similar question 2"},
                ),
            ]
            mock_result.items[0].get_display_text.return_value = "Similar question 1"
            mock_result.items[1].get_display_text.return_value = "Similar question 2"
            mock_result.get_best_result.return_value = mock_result.items[0]

            mock_controller.search = AsyncMock(return_value=mock_result)

            result = await search_memory(
                query="Test question", tool_context=self.mock_tool_context
            )

            assert result["success"] is True
            assert result["found"] is True
            assert result["total_found"] == 2

    @pytest.mark.asyncio
    async def test_search_memory_not_found(self):
        """Test search when no results are found."""
        with patch(
            "aigise.memory.tools.memory_search_tools._get_search_controller"
        ) as mock_controller_factory:
            mock_controller = MagicMock()
            mock_controller_factory.return_value = mock_controller

            mock_result = MagicMock()
            mock_result.has_results = False
            mock_result.total_found = 0
            mock_result.strategy_used = "embedding_search"
            mock_result.iterations = 1
            mock_result.items = []
            mock_result.get_best_result.return_value = None

            mock_controller.search = AsyncMock(return_value=mock_result)

            result = await search_memory(
                query="Unique question", tool_context=self.mock_tool_context
            )

            assert result["success"] is True
            assert result["found"] is False


class TestListMemoryContents:
    """Test listing memory contents."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_tool_context = MagicMock()
        self.mock_neo4j_client = AsyncMock()

        self.mock_get_client_patcher = patch(
            "aigise.memory.tools.memory_search_tools.get_neo4j_client_from_context"
        )
        self.mock_get_client = self.mock_get_client_patcher.start()
        self.mock_get_client.return_value = self.mock_neo4j_client

    def teardown_method(self):
        """Clean up patches."""
        self.mock_get_client_patcher.stop()

    @pytest.mark.asyncio
    async def test_list_memory_contents_success(self):
        """Test successful listing of memory contents."""
        # Mock count and list queries
        self.mock_neo4j_client.run_query.side_effect = [
            [{"total": 2}],  # Count for Question
            [  # List for Question
                {"props": {"text": "Question 1", "created_at": "2024-01-01"}},
                {"props": {"text": "Question 2", "created_at": "2024-01-02"}},
            ],
            [{"total": 1}],  # Count for Topic
            [  # List for Topic
                {"props": {"name": "Topic 1"}},
            ],
        ]

        result = await list_memory_contents(tool_context=self.mock_tool_context)

        assert result["success"] is True
        assert "contents" in result
        assert "totals" in result


class TestCacheQaPair:
    """Test caching Q&A pairs."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_tool_context = MagicMock()
        self.mock_neo4j_client = AsyncMock()

        self.mock_get_client_patcher = patch(
            "aigise.memory.tools.memory_update_tools.get_neo4j_client_from_context"
        )
        self.mock_get_client = self.mock_get_client_patcher.start()
        self.mock_get_client.return_value = self.mock_neo4j_client

        self.mock_get_session_id_patcher = patch(
            "aigise.memory.tools.memory_update_tools.get_aigise_session_id_from_context"
        )
        self.mock_get_session_id = self.mock_get_session_id_patcher.start()
        self.mock_get_session_id.return_value = "test-session-id"

    def teardown_method(self):
        """Clean up patches."""
        self.mock_get_client_patcher.stop()
        self.mock_get_session_id_patcher.stop()

    @pytest.mark.asyncio
    async def test_cache_qa_pair_success(self):
        """Test successful Q&A caching."""
        with patch(
            "aigise.memory.tools.memory_update_tools._get_update_controller"
        ) as mock_controller_factory:
            mock_controller = MagicMock()
            mock_controller_factory.return_value = mock_controller

            # Create mock update result
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.entities_added = 2
            mock_result.entities_updated = 0
            mock_result.relationships_added = 3
            mock_result.metadata = {
                "question_hash": "abc123",
                "answer_id": "ans-123",
            }
            mock_result.extracted_entities = [
                MagicMock(label="Topic", properties={"name": "Test Topic"}),
            ]
            mock_result.discovered_relationships = []

            mock_controller.store_qa_pair = AsyncMock(return_value=mock_result)

            result = await cache_qa_pair(
                question="What is foo?",
                answer="Foo is a function.",
                answering_agent="test-agent",
                answering_model="test-model",
                tool_context=self.mock_tool_context,
            )

            assert result["success"] is True
            assert result["entities_added"] == 2
            assert result["relationships_added"] == 3


class TestEnsureMemoryIndexes:
    """Test index creation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_tool_context = MagicMock()
        self.mock_neo4j_client = AsyncMock()

        self.mock_get_client_patcher = patch(
            "aigise.memory.tools.memory_update_tools.get_neo4j_client_from_context"
        )
        self.mock_get_client = self.mock_get_client_patcher.start()
        self.mock_get_client.return_value = self.mock_neo4j_client

    def teardown_method(self):
        """Clean up patches."""
        self.mock_get_client_patcher.stop()

    @pytest.mark.asyncio
    async def test_ensure_memory_indexes_success(self):
        """Test successful index creation."""
        self.mock_neo4j_client.run_query.return_value = None

        result = await ensure_memory_indexes(tool_context=self.mock_tool_context)

        assert result["success"] is True
        # Should create multiple indexes
        assert self.mock_neo4j_client.run_query.call_count > 0


class TestDeleteFromMemory:
    """Test deleting entities from memory."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_tool_context = MagicMock()
        self.mock_neo4j_client = AsyncMock()

        self.mock_get_client_patcher = patch(
            "aigise.memory.tools.memory_update_tools.get_neo4j_client_from_context"
        )
        self.mock_get_client = self.mock_get_client_patcher.start()
        self.mock_get_client.return_value = self.mock_neo4j_client

    def teardown_method(self):
        """Clean up patches."""
        self.mock_get_client_patcher.stop()

    @pytest.mark.asyncio
    async def test_delete_entity_success(self):
        """Test successful entity deletion."""
        with patch(
            "aigise.memory.tools.memory_update_tools._get_update_controller"
        ) as mock_controller_factory:
            mock_controller = MagicMock()
            mock_controller_factory.return_value = mock_controller

            # Create mock delete result
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.error = None

            mock_controller.delete_entity = AsyncMock(return_value=mock_result)

            result = await delete_from_memory(
                node_label="Topic",
                node_key={"name": "test_topic"},
                tool_context=self.mock_tool_context,
            )

            assert result["success"] is True
            assert "Successfully deleted" in result["message"]

    @pytest.mark.asyncio
    async def test_delete_entity_not_found(self):
        """Test deletion when entity doesn't exist."""
        with patch(
            "aigise.memory.tools.memory_update_tools._get_update_controller"
        ) as mock_controller_factory:
            mock_controller = MagicMock()
            mock_controller_factory.return_value = mock_controller

            mock_result = MagicMock()
            mock_result.success = False
            mock_result.error = "No matching node found"

            mock_controller.delete_entity = AsyncMock(return_value=mock_result)

            result = await delete_from_memory(
                node_label="Topic",
                node_key={"name": "nonexistent_topic"},
                tool_context=self.mock_tool_context,
            )

            assert result["success"] is False
            assert "error" in result


class TestDeleteRelationshipFromMemory:
    """Test deleting relationships from memory."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_tool_context = MagicMock()
        self.mock_neo4j_client = AsyncMock()

        self.mock_get_client_patcher = patch(
            "aigise.memory.tools.memory_update_tools.get_neo4j_client_from_context"
        )
        self.mock_get_client = self.mock_get_client_patcher.start()
        self.mock_get_client.return_value = self.mock_neo4j_client

    def teardown_method(self):
        """Clean up patches."""
        self.mock_get_client_patcher.stop()

    @pytest.mark.asyncio
    async def test_delete_relationship_success(self):
        """Test successful relationship deletion."""
        with patch(
            "aigise.memory.tools.memory_update_tools._get_update_controller"
        ) as mock_controller_factory:
            mock_controller = MagicMock()
            mock_controller_factory.return_value = mock_controller

            mock_result = MagicMock()
            mock_result.success = True
            mock_result.error = None

            mock_controller.delete_relationship = AsyncMock(return_value=mock_result)

            result = await delete_relationship_from_memory(
                relationship_type="HAS_TOPIC",
                source_label="Question",
                source_key={"question_hash": "abc123"},
                target_label="Topic",
                target_key={"name": "test_topic"},
                tool_context=self.mock_tool_context,
            )

            assert result["success"] is True
            assert "Successfully deleted" in result["message"]
