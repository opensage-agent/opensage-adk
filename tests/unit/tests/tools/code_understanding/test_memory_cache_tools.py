"""Unit tests for memory_cache_tools module."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aigise.toolbox.code_understanding.memory_cache_tools import (
    EMBEDDING_DIMENSION,
    _hash_question,
    cache_qa_pair,
    get_cached_answer_by_id,
    list_cached_questions,
    lookup_similar_answers,
)


class TestHashQuestion:
    """Test question hashing utility."""

    def test_hash_question_consistent(self):
        """Test that same question produces same hash."""
        question = "What is the caller of function foo?"
        hash1 = _hash_question(question)
        hash2 = _hash_question(question)
        assert hash1 == hash2

    def test_hash_question_strips_whitespace(self):
        """Test that whitespace is stripped before hashing."""
        question1 = "What is the caller?"
        question2 = "  What is the caller?  "
        assert _hash_question(question1) == _hash_question(question2)

    def test_hash_question_different_questions(self):
        """Test that different questions produce different hashes."""
        hash1 = _hash_question("Question A")
        hash2 = _hash_question("Question B")
        assert hash1 != hash2

    def test_hash_question_sha256_length(self):
        """Test that hash is correct SHA256 length (64 hex chars)."""
        hash_result = _hash_question("Test question")
        assert len(hash_result) == 64


class TestListCachedQuestions:
    """Test listing cached questions."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_tool_context = MagicMock()
        self.mock_neo4j_client = AsyncMock()

        # Mock get_neo4j_client_from_context
        self.mock_get_client_patcher = patch(
            "aigise.toolbox.code_understanding.memory_cache_tools.get_neo4j_client_from_context"
        )
        self.mock_get_client = self.mock_get_client_patcher.start()
        self.mock_get_client.return_value = self.mock_neo4j_client

    def teardown_method(self):
        """Clean up patches."""
        self.mock_get_client_patcher.stop()

    @pytest.mark.asyncio
    async def test_list_cached_questions_success(self):
        """Test successful listing of cached questions."""
        # Mock count query
        self.mock_neo4j_client.run_query.side_effect = [
            [{"total": 2}],  # Count query
            [  # List query
                {
                    "qa_id": "id-1",
                    "question": "Question 1",
                    "cached_at": "2024-01-01T00:00:00",
                    "access_count": 5,
                },
                {
                    "qa_id": "id-2",
                    "question": "Question 2",
                    "cached_at": "2024-01-02T00:00:00",
                    "access_count": 3,
                },
            ],
        ]

        result = await list_cached_questions(tool_context=self.mock_tool_context)

        assert result["success"] is True
        assert result["total"] == 2
        assert len(result["items"]) == 2
        assert result["items"][0]["qa_id"] == "id-1"
        assert result["items"][1]["question"] == "Question 2"

    @pytest.mark.asyncio
    async def test_list_cached_questions_empty(self):
        """Test listing when no cached questions exist."""
        self.mock_neo4j_client.run_query.side_effect = [
            [{"total": 0}],  # Count query
            [],  # List query
        ]

        result = await list_cached_questions(tool_context=self.mock_tool_context)

        assert result["success"] is True
        assert result["total"] == 0
        assert result["items"] == []

    @pytest.mark.asyncio
    async def test_list_cached_questions_pagination(self):
        """Test listing with pagination parameters."""
        self.mock_neo4j_client.run_query.side_effect = [
            [{"total": 100}],
            [
                {
                    "qa_id": "id-50",
                    "question": "Q50",
                    "cached_at": "2024-01-01",
                    "access_count": 1,
                }
            ],
        ]

        result = await list_cached_questions(
            tool_context=self.mock_tool_context, limit=10, offset=50
        )

        assert result["limit"] == 10
        assert result["offset"] == 50

        # Verify pagination params were passed to query
        call_args = self.mock_neo4j_client.run_query.call_args_list[1]
        query_params = call_args[0][1]
        assert query_params["limit"] == 10
        assert query_params["offset"] == 50


class TestGetCachedAnswerById:
    """Test getting cached answer by ID."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_tool_context = MagicMock()
        self.mock_neo4j_client = AsyncMock()

        self.mock_get_client_patcher = patch(
            "aigise.toolbox.code_understanding.memory_cache_tools.get_neo4j_client_from_context"
        )
        self.mock_get_client = self.mock_get_client_patcher.start()
        self.mock_get_client.return_value = self.mock_neo4j_client

    def teardown_method(self):
        """Clean up patches."""
        self.mock_get_client_patcher.stop()

    @pytest.mark.asyncio
    async def test_get_cached_answer_found(self):
        """Test getting a cached answer that exists."""
        self.mock_neo4j_client.run_query.return_value = [
            {
                "question": "What is foo?",
                "answer": "Foo is a function.",
                "agent": "test-agent",
                "model": "test-model",
                "cached_at": "2024-01-01T00:00:00",
                "access_count": 5,
                "metadata": "{}",
            }
        ]

        result = await get_cached_answer_by_id(
            qa_id="test-id", tool_context=self.mock_tool_context
        )

        assert result["success"] is True
        assert result["found"] is True
        assert result["qa_id"] == "test-id"
        assert result["question"] == "What is foo?"
        assert result["answer"] == "Foo is a function."

    @pytest.mark.asyncio
    async def test_get_cached_answer_not_found(self):
        """Test getting a cached answer that doesn't exist."""
        self.mock_neo4j_client.run_query.return_value = []

        result = await get_cached_answer_by_id(
            qa_id="nonexistent-id", tool_context=self.mock_tool_context
        )

        assert result["success"] is True
        assert result["found"] is False
        assert result["qa_id"] == "nonexistent-id"


class TestLookupSimilarAnswers:
    """Test vector similarity search."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_tool_context = MagicMock()
        self.mock_neo4j_client = AsyncMock()

        self.mock_get_client_patcher = patch(
            "aigise.toolbox.code_understanding.memory_cache_tools.get_neo4j_client_from_context"
        )
        self.mock_get_client = self.mock_get_client_patcher.start()
        self.mock_get_client.return_value = self.mock_neo4j_client

        # Mock _ensure_indexes_once
        self.mock_ensure_indexes_patcher = patch(
            "aigise.toolbox.code_understanding.memory_cache_tools._ensure_indexes_once"
        )
        self.mock_ensure_indexes = self.mock_ensure_indexes_patcher.start()
        self.mock_ensure_indexes.return_value = None

        # Mock _generate_embedding
        self.mock_embedding_patcher = patch(
            "aigise.toolbox.code_understanding.memory_cache_tools._generate_embedding"
        )
        self.mock_generate_embedding = self.mock_embedding_patcher.start()
        self.mock_generate_embedding.return_value = [0.1] * EMBEDDING_DIMENSION

    def teardown_method(self):
        """Clean up patches."""
        self.mock_get_client_patcher.stop()
        self.mock_ensure_indexes_patcher.stop()
        self.mock_embedding_patcher.stop()

    @pytest.mark.asyncio
    async def test_lookup_similar_answers_found(self):
        """Test finding similar answers."""
        self.mock_neo4j_client.run_query.return_value = [
            {
                "question": "Similar question 1",
                "answer": "Answer 1",
                "agent": "agent-1",
                "model": "model-1",
                "hits": 3,
                "cached_at": "2024-01-01",
                "similarity": 0.95,
            },
            {
                "question": "Similar question 2",
                "answer": "Answer 2",
                "agent": "agent-2",
                "model": "model-2",
                "hits": 1,
                "cached_at": "2024-01-02",
                "similarity": 0.85,
            },
        ]

        result = await lookup_similar_answers(
            question="Test question", tool_context=self.mock_tool_context
        )

        assert result["success"] is True
        assert result["cached"] is True
        assert len(result["results"]) == 2
        assert result["top_answer"] == "Answer 1"
        assert result["top_similarity"] == 0.95

    @pytest.mark.asyncio
    async def test_lookup_similar_answers_not_found(self):
        """Test when no similar answers are found."""
        self.mock_neo4j_client.run_query.return_value = []

        result = await lookup_similar_answers(
            question="Unique question", tool_context=self.mock_tool_context
        )

        assert result["success"] is True
        assert result["cached"] is False
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_lookup_similar_answers_embedding_failure(self):
        """Test handling embedding generation failure."""
        self.mock_generate_embedding.side_effect = RuntimeError("API error")

        result = await lookup_similar_answers(
            question="Test question", tool_context=self.mock_tool_context
        )

        assert result["success"] is False
        assert result["cached"] is False
        assert "Failed to generate embedding" in result["error"]

    @pytest.mark.asyncio
    async def test_lookup_similar_answers_vector_search_failure(self):
        """Test handling vector search failure."""
        self.mock_neo4j_client.run_query.side_effect = RuntimeError(
            "Vector index not found"
        )

        result = await lookup_similar_answers(
            question="Test question", tool_context=self.mock_tool_context
        )

        assert result["success"] is False
        assert "Vector search failed" in result["error"]

    @pytest.mark.asyncio
    async def test_lookup_similar_answers_custom_params(self):
        """Test lookup with custom top_k and threshold."""
        self.mock_neo4j_client.run_query.return_value = []

        await lookup_similar_answers(
            question="Test",
            tool_context=self.mock_tool_context,
            top_k=5,
            similarity_threshold=0.9,
        )

        call_args = self.mock_neo4j_client.run_query.call_args
        query_params = call_args[0][1]
        assert query_params["top_k"] == 5
        assert query_params["threshold"] == 0.9


class TestCacheQaPair:
    """Test caching Q&A pairs."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_tool_context = MagicMock()
        self.mock_neo4j_client = AsyncMock()

        self.mock_get_client_patcher = patch(
            "aigise.toolbox.code_understanding.memory_cache_tools.get_neo4j_client_from_context"
        )
        self.mock_get_client = self.mock_get_client_patcher.start()
        self.mock_get_client.return_value = self.mock_neo4j_client

        self.mock_get_session_id_patcher = patch(
            "aigise.toolbox.code_understanding.memory_cache_tools.get_aigise_session_id_from_context"
        )
        self.mock_get_session_id = self.mock_get_session_id_patcher.start()
        self.mock_get_session_id.return_value = "test-session-id"

        # Mock _ensure_indexes_once
        self.mock_ensure_indexes_patcher = patch(
            "aigise.toolbox.code_understanding.memory_cache_tools._ensure_indexes_once"
        )
        self.mock_ensure_indexes = self.mock_ensure_indexes_patcher.start()
        self.mock_ensure_indexes.return_value = None

        # Mock _generate_embedding
        self.mock_embedding_patcher = patch(
            "aigise.toolbox.code_understanding.memory_cache_tools._generate_embedding"
        )
        self.mock_generate_embedding = self.mock_embedding_patcher.start()
        self.mock_generate_embedding.return_value = [0.1] * EMBEDDING_DIMENSION

    def teardown_method(self):
        """Clean up patches."""
        self.mock_get_client_patcher.stop()
        self.mock_get_session_id_patcher.stop()
        self.mock_ensure_indexes_patcher.stop()
        self.mock_embedding_patcher.stop()

    @pytest.mark.asyncio
    async def test_cache_qa_pair_created(self):
        """Test caching a new Q&A pair."""
        self.mock_neo4j_client.run_query.return_value = [
            {"qa_id": "new-id", "action": "created"}
        ]

        result = await cache_qa_pair(
            question="What is foo?",
            answer="Foo is a function.",
            answering_agent="test-agent",
            answering_model="test-model",
            tool_context=self.mock_tool_context,
        )

        assert result["success"] is True
        assert result["action"] == "created"
        assert result["embedding_stored"] is True

    @pytest.mark.asyncio
    async def test_cache_qa_pair_updated(self):
        """Test updating an existing Q&A pair."""
        self.mock_neo4j_client.run_query.return_value = [
            {"qa_id": "existing-id", "action": "updated"}
        ]

        result = await cache_qa_pair(
            question="What is foo?",
            answer="Updated answer.",
            answering_agent="test-agent",
            answering_model="test-model",
            tool_context=self.mock_tool_context,
        )

        assert result["success"] is True
        assert result["action"] == "updated"

    @pytest.mark.asyncio
    async def test_cache_qa_pair_without_embedding(self):
        """Test caching without storing embedding."""
        self.mock_neo4j_client.run_query.return_value = [
            {"qa_id": "new-id", "action": "created"}
        ]

        result = await cache_qa_pair(
            question="What is foo?",
            answer="Foo is a function.",
            answering_agent="test-agent",
            answering_model="test-model",
            tool_context=self.mock_tool_context,
            store_embedding=False,
        )

        assert result["success"] is True
        assert result["embedding_stored"] is False
        # Verify embedding was not generated
        self.mock_generate_embedding.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_qa_pair_embedding_failure(self):
        """Test caching when embedding generation fails."""
        self.mock_generate_embedding.side_effect = RuntimeError("API error")
        self.mock_neo4j_client.run_query.return_value = [
            {"qa_id": "new-id", "action": "created"}
        ]

        result = await cache_qa_pair(
            question="What is foo?",
            answer="Foo is a function.",
            answering_agent="test-agent",
            answering_model="test-model",
            tool_context=self.mock_tool_context,
        )

        # Should still succeed, just without embedding
        assert result["success"] is True
        assert result["embedding_stored"] is False
        assert "embedding_error" in result

    @pytest.mark.asyncio
    async def test_cache_qa_pair_with_metadata(self):
        """Test caching with custom metadata."""
        self.mock_neo4j_client.run_query.return_value = [
            {"qa_id": "new-id", "action": "created"}
        ]

        metadata = {"source": "test", "version": 1}

        await cache_qa_pair(
            question="What is foo?",
            answer="Foo is a function.",
            answering_agent="test-agent",
            answering_model="test-model",
            tool_context=self.mock_tool_context,
            metadata=metadata,
        )

        call_args = self.mock_neo4j_client.run_query.call_args
        query_params = call_args[0][1]
        assert json.loads(query_params["metadata"]) == metadata

    @pytest.mark.asyncio
    async def test_cache_qa_pair_ensures_indexes(self):
        """Test that caching ensures indexes are created."""
        self.mock_neo4j_client.run_query.return_value = [
            {"qa_id": "new-id", "action": "created"}
        ]

        await cache_qa_pair(
            question="What is foo?",
            answer="Answer",
            answering_agent="agent",
            answering_model="model",
            tool_context=self.mock_tool_context,
        )

        # Verify _ensure_indexes_once was called
        self.mock_ensure_indexes.assert_called_once_with(self.mock_tool_context)


class TestEnsureMemoryIndexes:
    """Test index creation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_tool_context = MagicMock()
        self.mock_neo4j_client = AsyncMock()

        self.mock_get_client_patcher = patch(
            "aigise.toolbox.code_understanding.memory_cache_tools.get_neo4j_client_from_context"
        )
        self.mock_get_client = self.mock_get_client_patcher.start()
        self.mock_get_client.return_value = self.mock_neo4j_client

    def teardown_method(self):
        """Clean up patches."""
        self.mock_get_client_patcher.stop()

    @pytest.mark.asyncio
    async def test_ensure_memory_indexes_success(self):
        """Test successful index creation."""
        from aigise.toolbox.code_understanding.memory_cache_tools import (
            ensure_memory_indexes,
        )

        self.mock_neo4j_client.run_query.return_value = None

        result = await ensure_memory_indexes(self.mock_tool_context)

        assert result is True
        # Should create 3 indexes: question_hash, question, and vector
        assert self.mock_neo4j_client.run_query.call_count == 3

    @pytest.mark.asyncio
    async def test_ensure_memory_indexes_vector_index_failure(self):
        """Test handling vector index creation failure."""
        from aigise.toolbox.code_understanding.memory_cache_tools import (
            ensure_memory_indexes,
        )

        # First two calls succeed (regular indexes), third fails (vector index)
        self.mock_neo4j_client.run_query.side_effect = [
            None,
            None,
            RuntimeError("Neo4j version too old"),
        ]

        result = await ensure_memory_indexes(self.mock_tool_context)

        # Should still return True (regular indexes succeeded)
        assert result is True

    @pytest.mark.asyncio
    async def test_ensure_memory_indexes_total_failure(self):
        """Test handling complete index creation failure."""
        from aigise.toolbox.code_understanding.memory_cache_tools import (
            ensure_memory_indexes,
        )

        self.mock_neo4j_client.run_query.side_effect = RuntimeError("Database error")

        result = await ensure_memory_indexes(self.mock_tool_context)

        assert result is False


class TestEmbeddingDimension:
    """Test embedding dimension constant."""

    def test_embedding_dimension_value(self):
        """Test that embedding dimension matches gemini-embedding-001."""
        # gemini-embedding-001 produces 3072-dimensional vectors
        assert EMBEDDING_DIMENSION == 3072
