"""Unit tests for DynamicAgentManager."""

from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from opensage.config.config_dataclass import OpenSageConfig
from opensage.session.opensage_dynamic_agent_manager import (
    AgentMetadata,
    AgentStatus,
    DynamicAgentManager,
)


class TestAgentStatus:
    """Test AgentStatus enum."""

    def test_agent_status_values(self):
        """Test that all agent status values exist."""
        assert AgentStatus.CREATED.value == "created"
        assert AgentStatus.ACTIVE.value == "active"
        assert AgentStatus.PAUSED.value == "paused"
        assert AgentStatus.STOPPED.value == "stopped"
        assert AgentStatus.ERROR.value == "error"
        assert AgentStatus.PENDING_TOOLS.value == "pending_tools"


class TestAgentMetadata:
    """Test AgentMetadata dataclass."""

    def test_agent_metadata_creation(self):
        """Test AgentMetadata creation with required fields."""
        now = datetime.now()
        metadata = AgentMetadata(
            id="test-id",
            name="test-agent",
            type="opensage_agent",
            status=AgentStatus.CREATED,
            created_at=now,
            updated_at=now,
        )

        assert metadata.id == "test-id"
        assert metadata.name == "test-agent"
        assert metadata.type == "opensage_agent"
        assert metadata.status == AgentStatus.CREATED
        assert metadata.created_at == now
        assert metadata.updated_at == now
        assert metadata.creator is None
        assert metadata.description is None
        assert metadata.config is None
        assert metadata.parent_id is None
        assert metadata.children_ids == []  # Should initialize empty list

    def test_agent_metadata_with_optional_fields(self):
        """Test AgentMetadata creation with optional fields."""
        now = datetime.now()
        config = {"model": "test-model", "temperature": 0.7}

        metadata = AgentMetadata(
            id="test-id",
            name="test-agent",
            type="opensage_agent",
            status=AgentStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            creator="test-creator",
            description="Test agent description",
            config=config,
            parent_id="parent-id",
            children_ids=["child1", "child2"],
        )

        assert metadata.creator == "test-creator"
        assert metadata.description == "Test agent description"
        assert metadata.config == config
        assert metadata.parent_id == "parent-id"
        assert metadata.children_ids == ["child1", "child2"]

    def test_agent_metadata_children_ids_default(self):
        """Test that children_ids defaults to empty list."""
        now = datetime.now()
        metadata = AgentMetadata(
            id="test-id",
            name="test-agent",
            type="opensage_agent",
            status=AgentStatus.CREATED,
            created_at=now,
            updated_at=now,
            children_ids=None,  # Explicitly set to None
        )

        assert metadata.children_ids == []  # Should be converted to empty list


class TestDynamicAgentManager:
    """Test DynamicAgentManager functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.opensage_session_id = "test-session-123"
        self.temp_dir = tempfile.mkdtemp()

        # Create test configuration
        self.config = OpenSageConfig()
        self.config.agent_storage_path = self.temp_dir

        # Create mock session
        class MockSession:
            def __init__(self, session_id, config):
                self.opensage_session_id = session_id
                self.config = config

        self.session = MockSession(self.opensage_session_id, self.config)
        self.manager = DynamicAgentManager(self.session)

    def teardown_method(self):
        """Clean up test fixtures."""
        import shutil

        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init_with_storage_path(self):
        """Test manager initialization with custom storage path."""
        assert self.manager.opensage_session_id == self.opensage_session_id
        assert self.manager.config == self.config
        assert self.manager.storage_path == Path(self.temp_dir)
        assert self.manager.storage_path.exists()

    def test_init_with_default_storage_path(self):
        """Test manager initialization with default storage path."""
        config = OpenSageConfig()
        config.agent_storage_path = None  # No storage path specified

        class MockSession:
            def __init__(self, session_id, config):
                self.opensage_session_id = session_id
                self.config = config

        session = MockSession("test-session", config)
        manager = DynamicAgentManager(session)

        assert (
            manager.storage_path
            == Path("~/.local/opensage/dynamic_agents").expanduser()
        )

    def test_init_with_empty_storage_path(self):
        """Test manager initialization with empty storage path."""
        config = OpenSageConfig()
        config.agent_storage_path = ""  # Empty string

        class MockSession:
            def __init__(self, session_id, config):
                self.opensage_session_id = session_id
                self.config = config

        session = MockSession("test-session", config)
        manager = DynamicAgentManager(session)

        assert (
            manager.storage_path
            == Path("~/.local/opensage/dynamic_agents").expanduser()
        )

    @patch("opensage.session.opensage_dynamic_agent_manager.OpenSageAgent")
    def test_create_agent_instance(self, mock_opensage_agent):
        """Test _create_agent_instance method."""
        mock_agent = MagicMock()
        mock_opensage_agent.return_value = mock_agent

        # Test with string model (should be wrapped)
        config = {
            "name": "test-agent",
            "model": "test-model-string",
            "instruction": "Test instruction",
        }

        with patch(
            "opensage.session.opensage_dynamic_agent_manager.LiteLlm"
        ) as mock_lite_llm:
            mock_model = MagicMock()
            mock_lite_llm.return_value = mock_model

            result = self.manager._create_agent_instance(**config)

            # Verify LiteLlm was called with the model string
            mock_lite_llm.assert_called_once_with(model="test-model-string")

            # Verify OpenSageAgent was called with wrapped model
            expected_config = config.copy()
            expected_config["model"] = mock_model
            mock_opensage_agent.assert_called_once_with(**expected_config)

            assert result == mock_agent

    def test_create_agent_instance_missing_required_params(self):
        """Test _create_agent_instance with missing required parameters."""
        with pytest.raises(ValueError, match="Missing required parameters"):
            self.manager._create_agent_instance(
                instruction="Test"
            )  # Missing name and model

    @pytest.mark.asyncio
    async def test_create_agent_success(self):
        """Test successful agent creation."""
        config = {
            "name": "test-agent",
            "model": "test-model",
            "instruction": "Test instruction",
            "tools": [],  # This should be filtered out of metadata_config
            "tool_names": [
                "tool1",
                "tool2",
            ],  # This should be filtered out of agent_config
        }

        with patch.object(self.manager, "_create_agent_instance") as mock_create:
            mock_agent = MagicMock()
            mock_agent.name = "test-agent"
            mock_create.return_value = mock_agent

            with patch.object(self.manager, "_persist_agent_metadata") as mock_persist:
                mock_persist.return_value = None

                agent_id, agent_instance = await self.manager.create_agent(
                    config, creator="test-creator"
                )

                # Verify agent was created
                assert isinstance(agent_id, str)
                assert agent_instance == mock_agent

                # Verify agent was stored
                assert agent_id in self.manager._agents
                assert agent_id in self.manager._metadata

                # Verify metadata
                metadata = self.manager._metadata[agent_id]
                assert metadata.name == "test-agent"
                assert metadata.type == "opensage_agent"
                assert metadata.status == AgentStatus.CREATED
                assert metadata.creator == "test-creator"
                assert "tools" not in metadata.config  # Should be filtered out
                assert "tool_names" in metadata.config  # Should be kept

                # Verify agent_config excludes tool_names
                mock_create.assert_called_once()
                call_args = mock_create.call_args[1]
                assert "tool_names" not in call_args
                assert "tools" in call_args

                # Verify persistence was called
                mock_persist.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_agent_without_persistence(self):
        """Test agent creation without persistence."""
        config = {
            "name": "test-agent",
            "model": "test-model",
            "instruction": "Test instruction",
        }

        with patch.object(self.manager, "_create_agent_instance") as mock_create:
            mock_agent = MagicMock()
            mock_create.return_value = mock_agent

            with patch.object(self.manager, "_persist_agent_metadata") as mock_persist:
                await self.manager.create_agent(config, persist=False)

                # Verify persistence was not called
                mock_persist.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_agent_failure(self):
        """Test agent creation failure handling."""
        config = {"name": "test-agent", "model": "test-model"}

        with patch.object(self.manager, "_create_agent_instance") as mock_create:
            mock_create.side_effect = RuntimeError("Creation failed")

            with pytest.raises(RuntimeError, match="Creation failed"):
                await self.manager.create_agent(config)

    def test_get_agent(self):
        """Test getting agent by ID."""
        # Add test agent
        mock_agent = MagicMock()
        agent_id = "test-agent-id"
        self.manager._agents[agent_id] = mock_agent

        # Test getting existing agent
        result = self.manager.get_agent(agent_id)
        assert result == mock_agent

        # Test getting non-existent agent
        result = self.manager.get_agent("non-existent")
        assert result is None

    def test_get_agent_metadata(self):
        """Test getting agent metadata by ID."""
        # Add test metadata
        metadata = AgentMetadata(
            id="test-id",
            name="test-agent",
            type="opensage_agent",
            status=AgentStatus.CREATED,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        self.manager._metadata["test-id"] = metadata

        # Test getting existing metadata
        result = self.manager.get_agent_metadata("test-id")
        assert result == metadata

        # Test getting non-existent metadata
        result = self.manager.get_agent_metadata("non-existent")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_agent_status_success(self):
        """Test successful agent status update."""
        # Add test metadata
        metadata = AgentMetadata(
            id="test-id",
            name="test-agent",
            type="opensage_agent",
            status=AgentStatus.CREATED,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        self.manager._metadata["test-id"] = metadata

        with patch.object(self.manager, "_persist_agent_metadata") as mock_persist:
            result = await self.manager.update_agent_status(
                "test-id", AgentStatus.ACTIVE
            )

            assert result is True
            assert self.manager._metadata["test-id"].status == AgentStatus.ACTIVE

            # Verify persistence was called
            mock_persist.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_agent_status_not_found(self):
        """Test agent status update for non-existent agent."""
        result = await self.manager.update_agent_status(
            "non-existent", AgentStatus.ACTIVE
        )
        assert result is False

    def test_list_agents_no_filters(self):
        """Test listing all agents without filters."""
        # Add test metadata
        metadata1 = AgentMetadata(
            id="agent1",
            name="agent1",
            type="opensage_agent",
            status=AgentStatus.ACTIVE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            creator="creator1",
        )
        metadata2 = AgentMetadata(
            id="agent2",
            name="agent2",
            type="opensage_agent",
            status=AgentStatus.PAUSED,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            creator="creator2",
        )

        self.manager._metadata["agent1"] = metadata1
        self.manager._metadata["agent2"] = metadata2

        result = self.manager.list_agents()
        assert len(result) == 2
        assert metadata1 in result
        assert metadata2 in result

    def test_list_agents_with_status_filter(self):
        """Test listing agents with status filter."""
        # Add test metadata
        metadata1 = AgentMetadata(
            id="agent1",
            name="agent1",
            type="opensage_agent",
            status=AgentStatus.ACTIVE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        metadata2 = AgentMetadata(
            id="agent2",
            name="agent2",
            type="opensage_agent",
            status=AgentStatus.PAUSED,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        self.manager._metadata["agent1"] = metadata1
        self.manager._metadata["agent2"] = metadata2

        result = self.manager.list_agents(status=AgentStatus.ACTIVE)
        assert len(result) == 1
        assert result[0] == metadata1

    def test_list_agents_with_creator_filter(self):
        """Test listing agents with creator filter."""
        # Add test metadata
        metadata1 = AgentMetadata(
            id="agent1",
            name="agent1",
            type="opensage_agent",
            status=AgentStatus.ACTIVE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            creator="creator1",
        )
        metadata2 = AgentMetadata(
            id="agent2",
            name="agent2",
            type="opensage_agent",
            status=AgentStatus.ACTIVE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            creator="creator2",
        )

        self.manager._metadata["agent1"] = metadata1
        self.manager._metadata["agent2"] = metadata2

        result = self.manager.list_agents(creator="creator1")
        assert len(result) == 1
        assert result[0] == metadata1

    @pytest.mark.asyncio
    async def test_remove_agent_simple(self):
        """Test removing agent without cascade."""
        # Add test agent and metadata
        mock_agent = MagicMock()
        metadata = AgentMetadata(
            id="test-id",
            name="test-agent",
            type="opensage_agent",
            status=AgentStatus.ACTIVE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        self.manager._agents["test-id"] = mock_agent
        self.manager._metadata["test-id"] = metadata

        # Create mock metadata file
        metadata_file = self.manager.storage_path / "test-id_metadata.json"
        metadata_file.write_text("test content")

        result = await self.manager.remove_agent("test-id")

        assert result is True
        assert "test-id" not in self.manager._agents
        assert "test-id" not in self.manager._metadata
        assert not metadata_file.exists()

    @pytest.mark.asyncio
    async def test_remove_agent_not_found(self):
        """Test removing non-existent agent."""
        result = await self.manager.remove_agent("non-existent")
        assert result is False

    @pytest.mark.asyncio
    async def test_remove_agent_with_cascade(self):
        """Test removing agent with cascade delete of children."""
        # Add parent and child agents
        parent_metadata = AgentMetadata(
            id="parent-id",
            name="parent",
            type="opensage_agent",
            status=AgentStatus.ACTIVE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            children_ids=["child1-id", "child2-id"],
        )

        child1_metadata = AgentMetadata(
            id="child1-id",
            name="child1",
            type="opensage_agent",
            status=AgentStatus.ACTIVE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            parent_id="parent-id",
        )

        child2_metadata = AgentMetadata(
            id="child2-id",
            name="child2",
            type="opensage_agent",
            status=AgentStatus.ACTIVE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            parent_id="parent-id",
        )

        # Add to manager
        for agent_id in ["parent-id", "child1-id", "child2-id"]:
            self.manager._agents[agent_id] = MagicMock()

        self.manager._metadata["parent-id"] = parent_metadata
        self.manager._metadata["child1-id"] = child1_metadata
        self.manager._metadata["child2-id"] = child2_metadata

        result = await self.manager.remove_agent("parent-id", cascade=True)

        assert result is True
        # All agents should be removed
        assert "parent-id" not in self.manager._agents
        assert "child1-id" not in self.manager._agents
        assert "child2-id" not in self.manager._agents

    @pytest.mark.asyncio
    async def test_remove_agent_updates_parent(self):
        """Test that removing child updates parent's children list."""
        # Add parent and child
        parent_metadata = AgentMetadata(
            id="parent-id",
            name="parent",
            type="opensage_agent",
            status=AgentStatus.ACTIVE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            children_ids=["child-id", "other-child-id"],
        )

        child_metadata = AgentMetadata(
            id="child-id",
            name="child",
            type="opensage_agent",
            status=AgentStatus.ACTIVE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            parent_id="parent-id",
        )

        self.manager._agents["parent-id"] = MagicMock()
        self.manager._agents["child-id"] = MagicMock()
        self.manager._metadata["parent-id"] = parent_metadata
        self.manager._metadata["child-id"] = child_metadata

        with patch.object(self.manager, "_persist_agent_metadata") as mock_persist:
            await self.manager.remove_agent("child-id")

            # Verify child was removed from parent's children list
            assert "child-id" not in parent_metadata.children_ids
            assert (
                "other-child-id" in parent_metadata.children_ids
            )  # Other child should remain

            # Verify parent metadata was persisted
            mock_persist.assert_called_once_with("parent-id", parent_metadata)

    def test_get_session_statistics(self):
        """Test getting session statistics."""
        # Add test metadata with different statuses
        metadata1 = AgentMetadata(
            id="agent1",
            name="agent1",
            type="opensage_agent",
            status=AgentStatus.ACTIVE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        metadata2 = AgentMetadata(
            id="agent2",
            name="agent2",
            type="opensage_agent",
            status=AgentStatus.ACTIVE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        metadata3 = AgentMetadata(
            id="agent3",
            name="agent3",
            type="opensage_agent",
            status=AgentStatus.PAUSED,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        self.manager._metadata["agent1"] = metadata1
        self.manager._metadata["agent2"] = metadata2
        self.manager._metadata["agent3"] = metadata3

        # Add corresponding agents
        for agent_id in ["agent1", "agent2", "agent3"]:
            self.manager._agents[agent_id] = MagicMock()

        stats = self.manager.get_session_statistics()

        assert stats["opensage_session_id"] == self.opensage_session_id
        assert stats["total_agents"] == 3
        assert stats["status_counts"]["active"] == 2
        assert stats["status_counts"]["paused"] == 1

    @pytest.mark.asyncio
    async def test_persist_agent_metadata(self):
        """Test persisting agent metadata to file."""
        now = datetime.now()
        metadata = AgentMetadata(
            id="test-id",
            name="test-agent",
            type="opensage_agent",
            status=AgentStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            creator="test-creator",
        )

        self.manager._persist_agent_metadata("test-id", metadata)

        # Verify file was created
        metadata_file = self.manager.storage_path / "test-id_metadata.json"
        assert metadata_file.exists()

        # Verify file contents
        with open(metadata_file, "r") as f:
            saved_data = json.load(f)

        assert saved_data["id"] == "test-id"
        assert saved_data["name"] == "test-agent"
        assert saved_data["status"] == "active"
        assert saved_data["creator"] == "test-creator"
        assert saved_data["created_at"] == now.isoformat()
        assert saved_data["updated_at"] == now.isoformat()

    def test_load_persisted_agents_on_demand_no_storage(self):
        """Test loading persisted agents when storage doesn't exist."""
        # Remove storage directory
        import shutil

        shutil.rmtree(self.temp_dir)

        # Should not raise exception
        self.manager._load_persisted_agents_on_demand({})

    def test_load_persisted_agents_on_demand_already_loaded(self):
        """Test that already loaded agents are skipped."""
        # Add agent to manager (simulate already loaded)
        agent_id = "test-agent-id"
        metadata = AgentMetadata(
            id=agent_id,
            name="test-agent",
            type="opensage_agent",
            status=AgentStatus.ACTIVE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        self.manager._agents[agent_id] = MagicMock()
        self.manager._metadata[agent_id] = metadata

        # Create metadata file
        metadata_file = self.manager.storage_path / f"{agent_id}_metadata.json"
        with open(metadata_file, "w") as f:
            json.dump(
                {
                    "id": agent_id,
                    "name": "test-agent",
                    "type": "opensage_agent",
                    "status": "active",
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                },
                f,
            )

        # Should skip loading since agent is already loaded
        initial_metadata = self.manager._metadata[agent_id]
        self.manager._load_persisted_agents_on_demand({})

        # Metadata should be unchanged (not reloaded from file)
        assert self.manager._metadata[agent_id] is initial_metadata

    def test_try_rebuild_agent_no_tools_required(self):
        """Test rebuilding agent that requires no tools."""
        metadata = AgentMetadata(
            id="test-id",
            name="test-agent",
            type="opensage_agent",
            status=AgentStatus.PENDING_TOOLS,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            config={
                "name": "test-agent",
                "model": "test-model",
                "tool_names": [],  # No tools required
            },
        )

        with patch.object(self.manager, "_create_agent_instance") as mock_create:
            mock_agent = MagicMock()
            mock_create.return_value = mock_agent

            self.manager._try_rebuild_agent_with_caller_tools(metadata, {})

            # Verify agent was created and stored
            assert "test-id" in self.manager._agents
            assert self.manager._agents["test-id"] == mock_agent
            assert metadata.status == AgentStatus.ACTIVE

            # Verify agent was created with empty tools list
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["tools"] == []

    def test_try_rebuild_agent_missing_tools(self):
        """Test rebuilding agent when required tools are missing."""
        metadata = AgentMetadata(
            id="test-id",
            name="test-agent",
            type="opensage_agent",
            status=AgentStatus.CREATED,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            config={
                "name": "test-agent",
                "model": "test-model",
                "tool_names": ["tool1", "tool2"],
            },
        )

        # Only provide tool1, missing tool2
        caller_tools = {"tool1": MagicMock()}

        self.manager._try_rebuild_agent_with_caller_tools(metadata, caller_tools)

        # Agent should not be created, status should be PENDING_TOOLS
        assert "test-id" not in self.manager._agents
        assert metadata.status == AgentStatus.PENDING_TOOLS

    def test_try_rebuild_agent_with_tools_success(self):
        """Test successful agent rebuilding with tools."""
        metadata = AgentMetadata(
            id="test-id",
            name="test-agent",
            type="opensage_agent",
            status=AgentStatus.PENDING_TOOLS,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            config={
                "name": "test-agent",
                "model": "test-model",
                "tool_names": ["tool1", "tool2"],
            },
        )

        # Provide all required tools
        tool1 = MagicMock()
        tool2 = MagicMock()
        caller_tools = {"tool1": tool1, "tool2": tool2}

        with patch.object(self.manager, "_create_agent_instance") as mock_create:
            mock_agent = MagicMock()
            mock_create.return_value = mock_agent

            self.manager._try_rebuild_agent_with_caller_tools(metadata, caller_tools)

            # Verify agent was created and stored
            assert "test-id" in self.manager._agents
            assert self.manager._agents["test-id"] == mock_agent
            assert metadata.status == AgentStatus.ACTIVE

            # Verify agent was created with correct tools
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["tools"] == [tool1, tool2]

    def test_try_rebuild_agent_creation_failure(self):
        """Test agent rebuilding when creation fails."""
        metadata = AgentMetadata(
            id="test-id",
            name="test-agent",
            type="opensage_agent",
            status=AgentStatus.PENDING_TOOLS,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            config={"name": "test-agent", "model": "test-model", "tool_names": []},
        )

        with patch.object(self.manager, "_create_agent_instance") as mock_create:
            mock_create.side_effect = RuntimeError("Creation failed")

            self.manager._try_rebuild_agent_with_caller_tools(metadata, {})

            # Agent should not be created, status should be ERROR
            assert "test-id" not in self.manager._agents
            assert metadata.status == AgentStatus.ERROR

    def test_cleanup(self):
        """Test manager cleanup."""
        # Add some test data
        self.manager._agents["test1"] = MagicMock()
        self.manager._metadata["test1"] = MagicMock()

        # Cleanup
        self.manager.cleanup()

        # Verify everything was cleared
        assert len(self.manager._agents) == 0
        assert len(self.manager._metadata) == 0
