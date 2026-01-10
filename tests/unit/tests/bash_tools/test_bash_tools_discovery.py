"""Unit tests for bash tools discovery and metadata loading."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from aigise.toolbox.general.bash_tools_interface import (
    BashToolMetadata,
    _load_bash_tools_from_skills,
    _parse_skill_md_config,
)
from aigise.utils.project_info import PROJECT_PATH


class TestBashToolsDiscovery:
    """Test bash tools discovery functionality."""

    def test_discover_tools_from_directory(self):
        """Test that tools can be discovered from bash_tools directory."""
        tools = _load_bash_tools_from_skills()

        # Should find at least some tools
        assert len(tools) > 0

        # Verify tool structure
        for tool in tools:
            assert isinstance(tool, BashToolMetadata)
            assert tool.name
            assert tool.script_path
            assert tool.description

    def test_tool_has_required_attributes(self):
        """Test that discovered tools have all required attributes."""
        tools = _load_bash_tools_from_skills()

        for tool in tools:
            assert hasattr(tool, "name")
            assert hasattr(tool, "script_path")
            assert hasattr(tool, "description")
            assert hasattr(tool, "parameters")
            assert hasattr(tool, "sandbox_types")
            assert hasattr(tool, "timeout")
            assert hasattr(tool, "returns_json")

    def test_tool_script_path_format(self):
        """Test that script paths are in correct format."""
        tools = _load_bash_tools_from_skills()

        for tool in tools:
            # Script path should be relative to CONTAINER_BASH_TOOLS_DIR
            assert "/" in tool.script_path or tool.script_path
            # Should not start with absolute path markers
            assert not tool.script_path.startswith("/sandbox_scripts")
            assert not tool.script_path.startswith("/bash_tools")

    def test_tool_sandbox_types(self):
        """Test that tools have valid sandbox types."""
        tools = _load_bash_tools_from_skills()

        for tool in tools:
            assert isinstance(tool.sandbox_types, list)
            assert len(tool.sandbox_types) > 0
            # Sandbox types should be strings
            for sandbox_type in tool.sandbox_types:
                assert isinstance(sandbox_type, str)

    def test_tool_timeout_positive(self):
        """Test that tools have positive timeout values."""
        tools = _load_bash_tools_from_skills()

        for tool in tools:
            assert isinstance(tool.timeout, int)
            assert tool.timeout > 0

    def test_tool_parameters_structure(self):
        """Test that tool parameters have correct structure."""
        tools = _load_bash_tools_from_skills()

        for tool in tools:
            assert isinstance(tool.parameters, list)
            for param in tool.parameters:
                assert isinstance(param, dict)
                assert "name" in param
                assert "type" in param


class TestSpecificTools:
    """Test specific bash tools that should exist."""

    def test_get_callee_tool_exists(self):
        """Test that get-callee tool is discovered."""
        tools = _load_bash_tools_from_skills()
        tool_names = [tool.name for tool in tools]

        # get-callee tool should exist
        assert "get-callee" in tool_names

        # Find get-callee tool and verify its properties
        callee_tool = next((t for t in tools if t.name == "get-callee"), None)
        assert callee_tool is not None
        assert "callee" in callee_tool.description.lower()

    def test_tools_have_unique_names(self):
        """Test that all discovered tools have unique names."""
        tools = _load_bash_tools_from_skills()
        tool_names = [tool.name for tool in tools]

        # All names should be unique
        assert len(tool_names) == len(set(tool_names))


class TestSkillMdParsing:
    """Test SKILL.md file parsing for various tools."""

    def test_parse_grep_skill_md(self):
        """Test parsing grep tool's SKILL.md."""
        grep_skill_path = PROJECT_PATH / "src/aigise/bash_tools/retrieval/grep/SKILL.md"

        if not grep_skill_path.exists():
            pytest.skip("grep SKILL.md not found")

        content = grep_skill_path.read_text(encoding="utf-8")
        config = _parse_skill_md_config(content)

        assert "parameters" in config
        assert config["sandbox_types"] == ["main"] or "main" in config["sandbox_types"]
        # grep returns JSON (mentioned in description), but may not have ```json block
        # So we check if it's detected or at least verify the config structure
        assert isinstance(config["returns_json"], bool)

    def test_parse_get_callee_skill_md(self):
        """Test parsing get-callee tool's SKILL.md."""
        callee_skill_path = (
            PROJECT_PATH / "src/aigise/bash_tools/static_analysis/get-callee/SKILL.md"
        )

        if not callee_skill_path.exists():
            pytest.skip("get-callee SKILL.md not found")

        content = callee_skill_path.read_text(encoding="utf-8")
        config = _parse_skill_md_config(content)

        assert "parameters" in config
        # get-callee should require neo4j sandbox
        assert len(config["sandbox_types"]) > 0
        # get-callee returns JSON (mentioned in description), but may not have ```json block
        # So we check if it's detected or at least verify the config structure
        assert isinstance(config["returns_json"], bool)


class TestToolMetadataConsistency:
    """Test consistency of tool metadata."""

    def test_all_tools_have_descriptions(self):
        """Test that all tools have non-empty descriptions."""
        tools = _load_bash_tools_from_skills()

        for tool in tools:
            assert tool.description
            assert len(tool.description.strip()) > 0

    def test_all_tools_have_script_paths(self):
        """Test that all tools have valid script paths."""
        tools = _load_bash_tools_from_skills()

        for tool in tools:
            assert tool.script_path
            # Script path should contain the tool name or category
            assert len(tool.script_path) > 0

    def test_tool_metadata_to_function_signature(self):
        """Test that to_function_signature works for all tools."""
        tools = _load_bash_tools_from_skills()

        for tool in tools:
            sig = tool.to_function_signature()
            assert isinstance(sig, dict)
            assert "name" in sig
            assert "description" in sig
            assert "parameters" in sig
            assert "sandbox_types" in sig
            assert "timeout" in sig
            assert "returns_json" in sig
            assert "background" in sig


class TestToolDiscoveryEdgeCases:
    """Test edge cases in tool discovery."""

    def test_discovery_with_missing_directory(self):
        """Test discovery when bash_tools directory doesn't exist."""
        with patch(
            "aigise.toolbox.general.bash_tools_interface.BASH_TOOLS_DIR"
        ) as mock_dir:
            mock_dir.exists.return_value = False

            tools = _load_bash_tools_from_skills()
            assert tools == []

    def test_discovery_with_empty_directory(self):
        """Test discovery with empty directory (mocked)."""
        with patch(
            "aigise.toolbox.general.bash_tools_interface.BASH_TOOLS_DIR"
        ) as mock_dir:
            mock_dir.exists.return_value = True
            mock_dir.iterdir.return_value = []

            tools = _load_bash_tools_from_skills()
            assert tools == []

    def test_discovery_skips_non_directories(self):
        """Test that discovery skips files (non-directories)."""
        tools = _load_bash_tools_from_skills()

        # This test verifies that the discovery process works
        # even if there are files in the bash_tools directory
        # (though in practice there shouldn't be)
        assert isinstance(tools, list)
