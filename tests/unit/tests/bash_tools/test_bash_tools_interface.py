"""Unit tests for bash_tools_interface module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aigise.toolbox.general.bash_tools_interface import (
    BashToolMetadata,
    _load_bash_tools_from_skills,
    _parse_skill_md_config,
    get_background_task_output,
    list_available_scripts,
    list_background_tasks,
    run_bash_tool_script,
    run_terminal_command,
)


class TestBashToolMetadata:
    """Test BashToolMetadata class."""

    def test_bash_tool_metadata_init(self):
        """Test BashToolMetadata initialization."""
        metadata = BashToolMetadata(
            name="test_tool",
            script_path="test_tool/scripts/test_tool.sh",
            description="Test tool description",
            parameters=[
                {
                    "name": "arg1",
                    "type": "str",
                    "description": "First argument",
                    "required": True,
                }
            ],
            sandbox_types=["main"],
            timeout=30,
            returns_json=True,
        )

        assert metadata.name == "test_tool"
        assert metadata.script_path == "test_tool/scripts/test_tool.sh"
        assert metadata.description == "Test tool description"
        assert len(metadata.parameters) == 1
        assert metadata.sandbox_types == ["main"]
        assert metadata.timeout == 30
        assert metadata.returns_json is True

    def test_bash_tool_metadata_to_function_signature(self):
        """Test to_function_signature method."""
        metadata = BashToolMetadata(
            name="test_tool",
            script_path="test_tool/scripts/test_tool.sh",
            description="Test tool description",
            parameters=[],
        )

        sig = metadata.to_function_signature()
        assert sig["name"] == "test_tool"
        assert sig["description"] == "Test tool description"
        assert sig["background"] is False


class TestParseSkillMdConfig:
    """Test _parse_skill_md_config function."""

    def test_parse_basic_skill_md(self):
        """Test parsing basic SKILL.md content."""
        content = """
---
name: test-tool
description: A test tool
---

# Test Tool

## Parameters

### arg1 (required, positional position 0)

**Type**: `str`

First argument

## Return Value

```json
{"result": "success"}
```

## Timeout

30 seconds
"""
        config = _parse_skill_md_config(content)

        assert len(config["parameters"]) == 1
        assert config["parameters"][0]["name"] == "arg1"
        assert config["parameters"][0]["type"] == "str"
        assert config["parameters"][0]["required"] is True
        assert config["parameters"][0]["positional"] is True
        assert config["parameters"][0]["position"] == 0
        assert config["timeout"] == 30
        assert config["returns_json"] is True

    def test_parse_named_parameters(self):
        """Test parsing named parameters."""
        content = """
## Parameters

### flag (optional)

**Type**: `bool`

A boolean flag

### value (required)

**Type**: `int`

An integer value
"""
        config = _parse_skill_md_config(content)

        assert len(config["parameters"]) == 2
        assert config["parameters"][0]["name"] == "flag"
        assert config["parameters"][0]["type"] == "bool"
        assert config["parameters"][1]["name"] == "value"
        assert config["parameters"][1]["type"] == "int"

    def test_parse_list_parameter(self):
        """Test parsing list parameter."""
        content = """
## Parameters

### items (required, positional position 0)

**Type**: `list[str]`

List of items
"""
        config = _parse_skill_md_config(content)

        assert len(config["parameters"]) == 1
        assert config["parameters"][0]["type"] == "list"

    def test_parse_default_values(self):
        """Test parsing default values."""
        content = """
## Parameters

### timeout (optional)

**Type**: `int`

Timeout value, default: 60
"""
        config = _parse_skill_md_config(content)

        assert len(config["parameters"]) == 1
        assert config["parameters"][0].get("default") == 60


class TestLoadBashToolsFromSkills:
    """Test _load_bash_tools_from_skills function."""

    def test_load_tools_from_existing_directory(self):
        """Test loading tools from existing bash_tools directory."""
        tools = _load_bash_tools_from_skills()

        # Should find at least some tools
        assert len(tools) > 0

        # Check that all tools have required attributes
        for tool in tools:
            assert hasattr(tool, "name")
            assert hasattr(tool, "script_path")
            assert hasattr(tool, "description")
            assert hasattr(tool, "parameters")
            assert hasattr(tool, "sandbox_types")
            assert hasattr(tool, "timeout")
            assert hasattr(tool, "returns_json")

    def test_tool_metadata_structure(self):
        """Test that loaded tools have correct structure."""
        tools = _load_bash_tools_from_skills()

        if len(tools) > 0:
            tool = tools[0]
            assert isinstance(tool.name, str)
            assert isinstance(tool.script_path, str)
            assert isinstance(tool.description, str)
            assert isinstance(tool.parameters, list)
            assert isinstance(tool.sandbox_types, list)
            assert isinstance(tool.timeout, int)
            assert isinstance(tool.returns_json, bool)


class TestRunBashToolScript:
    """Test run_bash_tool_script function."""

    @pytest.fixture
    def mock_sandbox(self):
        """Create a mock sandbox."""
        sandbox = MagicMock()
        sandbox.run_command_in_container = MagicMock(return_value=("test output", 0))
        return sandbox

    @pytest.fixture
    def mock_task_manager(self):
        """Create a mock task manager."""
        task_manager = MagicMock()
        task_manager.start_bg_task = MagicMock(return_value=("task_123", "Started"))
        task_manager.wait_for_task = MagicMock(return_value=True)
        task_manager.get_task_output = MagicMock(return_value='{"result": "success"}')
        task_manager.get_task_exit_code = MagicMock(return_value=0)
        return task_manager

    def test_run_bash_tool_script_with_sandbox(self, mock_sandbox, mock_task_manager):
        """Test run_bash_tool_script with direct sandbox."""
        mock_sandbox.bash_tasks = mock_task_manager

        output, exit_code = run_bash_tool_script(
            script_name="test_script",
            args={"arg1": "value1"},
            sandbox=mock_sandbox,
            timeout=30,
        )

        assert exit_code == 0
        mock_task_manager.start_bg_task.assert_called_once()
        mock_task_manager.wait_for_task.assert_called_once()

    def test_run_bash_tool_script_with_tool_context(
        self, mock_sandbox, mock_task_manager
    ):
        """Test run_bash_tool_script with tool_context."""
        mock_context = MagicMock()
        mock_context.state = {"aigise_session_id": "test_session"}

        with (
            patch(
                "aigise.toolbox.general.bash_tools_interface.get_sandbox_from_context"
            ) as mock_get_sandbox,
            patch(
                "aigise.toolbox.general.bash_tools_interface.get_aigise_session"
            ) as mock_get_session,
        ):
            mock_get_sandbox.return_value = mock_sandbox

            mock_session = MagicMock()
            mock_session.bash_tasks = mock_task_manager
            mock_get_session.return_value = mock_session

            output, exit_code = run_bash_tool_script(
                script_name="test_script",
                args={"arg1": "value1"},
                tool_context=mock_context,
            )

            assert exit_code == 0
            mock_get_sandbox.assert_called_once()
            mock_get_session.assert_called_once()

    def test_run_bash_tool_script_missing_context_and_sandbox(self):
        """Test run_bash_tool_script returns error when both context and sandbox are None."""
        # @safe_tool_execution decorator catches exceptions and returns error dict
        result = run_bash_tool_script(
            script_name="test_script",
            args={},
            tool_context=None,
            sandbox=None,
        )

        # Should return error dict, not raise exception
        assert isinstance(result, dict)
        assert "error" in result or "success" in result
        # Check that error message contains the expected text
        error_msg = result.get("error", str(result))
        assert "tool_context or sandbox must be provided" in error_msg

    def test_run_bash_tool_script_with_param_definitions(
        self, mock_sandbox, mock_task_manager
    ):
        """Test run_bash_tool_script with parameter definitions."""
        mock_sandbox.bash_tasks = mock_task_manager

        param_definitions = [
            {
                "name": "arg1",
                "type": "str",
                "positional": True,
                "position": 0,
            },
            {
                "name": "flag",
                "type": "bool",
                "positional": False,
            },
        ]

        output, exit_code = run_bash_tool_script(
            script_name="test_script",
            args={"arg1": "value1", "flag": True},
            sandbox=mock_sandbox,
            param_definitions=param_definitions,
        )

        assert exit_code == 0
        # Check that command was built correctly
        call_args = mock_task_manager.start_bg_task.call_args
        command = call_args[0][1]  # Second argument is the command
        assert "value1" in command
        assert "--flag" in command

    def test_run_bash_tool_script_returns_json(self, mock_sandbox, mock_task_manager):
        """Test run_bash_tool_script with JSON parsing."""
        mock_sandbox.bash_tasks = mock_task_manager
        mock_task_manager.get_task_output.return_value = (
            '{"result": "success", "data": [1, 2, 3]}'
        )

        output, exit_code = run_bash_tool_script(
            script_name="test_script",
            args={},
            sandbox=mock_sandbox,
            returns_json=True,
        )

        assert exit_code == 0
        assert isinstance(output, dict)
        assert output["result"] == "success"

    def test_run_bash_tool_script_background(self, mock_sandbox, mock_task_manager):
        """Test run_bash_tool_script in background mode."""
        mock_sandbox.bash_tasks = mock_task_manager

        output, exit_code = run_bash_tool_script(
            script_name="test_script",
            args={},
            sandbox=mock_sandbox,
            background=True,
        )

        assert exit_code == 0
        mock_task_manager.start_bg_task.assert_called_once()
        # Should not wait for task in background mode
        mock_task_manager.wait_for_task.assert_not_called()

    def test_run_bash_tool_script_timeout(self, mock_sandbox, mock_task_manager):
        """Test run_bash_tool_script with timeout."""
        mock_sandbox.bash_tasks = mock_task_manager
        mock_task_manager.wait_for_task.return_value = False  # Timeout

        output, exit_code = run_bash_tool_script(
            script_name="test_script",
            args={},
            sandbox=mock_sandbox,
            timeout=5,
        )

        assert exit_code == 0
        assert "timed out" in output.lower()
        assert "task_123" in output


class TestListAvailableScripts:
    """Test list_available_scripts function."""

    def test_list_available_scripts(self):
        """Test listing available scripts."""
        mock_context = MagicMock()

        result = list_available_scripts(tool_context=mock_context)

        assert isinstance(result, str)
        assert "Available Bash Scripts" in result

    def test_list_available_scripts_no_tools(self):
        """Test list_available_scripts when no tools are found."""
        mock_context = MagicMock()

        with patch(
            "aigise.toolbox.general.bash_tools_interface._load_bash_tools_from_skills"
        ) as mock_load:
            mock_load.return_value = []

            result = list_available_scripts(tool_context=mock_context)

            assert "No bash tools found" in result


class TestRunTerminalCommand:
    """Test run_terminal_command function."""

    @pytest.fixture
    def mock_sandbox(self):
        """Create a mock sandbox."""
        sandbox = MagicMock()
        return sandbox

    @pytest.fixture
    def mock_task_manager(self):
        """Create a mock task manager."""
        task_manager = MagicMock()
        task_manager.start_bg_task = MagicMock(return_value=("task_123", "Started"))
        task_manager.wait_for_task = MagicMock(return_value=True)
        task_manager.get_task_output = MagicMock(return_value="command output")
        task_manager.get_task_exit_code = MagicMock(return_value=0)
        task_manager.cleanup_task = MagicMock(return_value=True)
        return task_manager

    def test_run_terminal_command_foreground(self, mock_sandbox, mock_task_manager):
        """Test run_terminal_command in foreground."""
        mock_context = MagicMock()
        mock_context.state = {"aigise_session_id": "test_session"}

        with (
            patch(
                "aigise.toolbox.general.bash_tools_interface.get_sandbox_from_context"
            ) as mock_get_sandbox,
            patch(
                "aigise.toolbox.general.bash_tools_interface.get_aigise_session"
            ) as mock_get_session,
        ):
            mock_get_sandbox.return_value = mock_sandbox

            mock_session = MagicMock()
            mock_session.bash_tasks = mock_task_manager
            mock_get_session.return_value = mock_session

            result = run_terminal_command(
                command="echo test",
                tool_context=mock_context,
            )

            assert result["success"] is True
            assert result["exit_code"] == 0
            assert "output" in result
            mock_task_manager.cleanup_task.assert_called_once()

    def test_run_terminal_command_background(self, mock_sandbox, mock_task_manager):
        """Test run_terminal_command in background."""
        mock_context = MagicMock()
        mock_context.state = {"aigise_session_id": "test_session"}

        with (
            patch(
                "aigise.toolbox.general.bash_tools_interface.get_sandbox_from_context"
            ) as mock_get_sandbox,
            patch(
                "aigise.toolbox.general.bash_tools_interface.get_aigise_session"
            ) as mock_get_session,
        ):
            mock_get_sandbox.return_value = mock_sandbox

            mock_session = MagicMock()
            mock_session.bash_tasks = mock_task_manager
            mock_get_session.return_value = mock_session

            result = run_terminal_command(
                command="echo test",
                background=True,
                tool_context=mock_context,
            )

            assert result["success"] is True
            assert result["status"] == "running"
            assert "task_id" in result
            # Should not wait or cleanup in background mode
            mock_task_manager.wait_for_task.assert_not_called()

    def test_run_terminal_command_timeout(self, mock_sandbox, mock_task_manager):
        """Test run_terminal_command with timeout."""
        mock_context = MagicMock()
        mock_context.state = {"aigise_session_id": "test_session"}
        mock_task_manager.wait_for_task.return_value = False  # Timeout

        with (
            patch(
                "aigise.toolbox.general.bash_tools_interface.get_sandbox_from_context"
            ) as mock_get_sandbox,
            patch(
                "aigise.toolbox.general.bash_tools_interface.get_aigise_session"
            ) as mock_get_session,
        ):
            mock_get_sandbox.return_value = mock_sandbox

            mock_session = MagicMock()
            mock_session.bash_tasks = mock_task_manager
            mock_get_session.return_value = mock_session

            result = run_terminal_command(
                command="echo test",
                timeout=5,
                tool_context=mock_context,
            )

            assert result["success"] is True
            assert result["timeout"] is True
            assert "task_id" in result

    def test_run_terminal_command_sandbox_error(self):
        """Test run_terminal_command when sandbox retrieval fails."""
        mock_context = MagicMock()
        mock_context.state = {"aigise_session_id": "test_session"}

        with patch(
            "aigise.toolbox.general.bash_tools_interface.get_sandbox_from_context"
        ) as mock_get_sandbox:
            mock_get_sandbox.side_effect = Exception("Sandbox not found")

            result = run_terminal_command(
                command="echo test",
                tool_context=mock_context,
            )

            assert result["success"] is False
            assert "error" in result

    def test_run_terminal_command_json_output(self, mock_sandbox, mock_task_manager):
        """Test run_terminal_command with JSON output."""
        mock_context = MagicMock()
        mock_context.state = {"aigise_session_id": "test_session"}
        mock_task_manager.get_task_output.return_value = '{"key": "value"}'

        with (
            patch(
                "aigise.toolbox.general.bash_tools_interface.get_sandbox_from_context"
            ) as mock_get_sandbox,
            patch(
                "aigise.toolbox.general.bash_tools_interface.get_aigise_session"
            ) as mock_get_session,
        ):
            mock_get_sandbox.return_value = mock_sandbox

            mock_session = MagicMock()
            mock_session.bash_tasks = mock_task_manager
            mock_get_session.return_value = mock_session

            result = run_terminal_command(
                command='echo \'{"key": "value"}\'',
                tool_context=mock_context,
            )

            assert result["success"] is True
            assert isinstance(result["output"], dict)
            assert result["output"]["key"] == "value"


class TestListBackgroundTasks:
    """Test list_background_tasks function."""

    def test_list_background_tasks_no_tasks(self):
        """Test list_background_tasks when no tasks exist."""
        mock_context = MagicMock()
        mock_context.state = {"aigise_session_id": "test_session"}

        with patch(
            "aigise.toolbox.general.bash_tools_interface.get_aigise_session"
        ) as mock_get_session:
            # Create a mock session without bash_tasks attribute
            # Use a regular object instead of MagicMock to avoid auto-attributes
            class MockSession:
                pass

            mock_session = MockSession()
            mock_get_session.return_value = mock_session

            result = list_background_tasks(tool_context=mock_context)

            assert "tasks" in result
            assert result["tasks"] == []
            assert "No background tasks" in result["summary"]

    def test_list_background_tasks_with_tasks(self):
        """Test list_background_tasks with existing tasks."""
        mock_context = MagicMock()
        mock_context.state = {"aigise_session_id": "test_session"}

        mock_task_manager = MagicMock()
        mock_task_manager.tasks = {
            "task_1": {
                "command": "echo test",
                "status": "running",
                "sandbox_name": "main",
            }
        }
        mock_task_manager.list_tasks = MagicMock(
            return_value=[
                {
                    "id": "task_1",
                    "command": "echo test",
                    "status": "running",
                    "sandbox": "main",
                }
            ]
        )

        with (
            patch(
                "aigise.toolbox.general.bash_tools_interface.get_aigise_session"
            ) as mock_get_session,
            patch(
                "aigise.toolbox.general.bash_tools_interface.get_sandbox_from_context"
            ) as mock_get_sandbox,
        ):
            mock_session = MagicMock()
            mock_session.bash_tasks = mock_task_manager
            mock_get_session.return_value = mock_session

            result = list_background_tasks(tool_context=mock_context)

            assert "tasks" in result
            assert len(result["tasks"]) == 1
            assert "summary" in result
            assert "Total: 1" in result["summary"]


class TestGetBackgroundTaskOutput:
    """Test get_background_task_output function."""

    def test_get_background_task_output_success(self):
        """Test get_background_task_output with successful task."""
        mock_context = MagicMock()
        mock_context.state = {"aigise_session_id": "test_session"}

        mock_task_manager = MagicMock()
        mock_task_manager.tasks = {
            "task_123": {
                "command": "echo test",
                "status": "completed",
                "sandbox_name": "main",
                "log_file": "/tmp/task_123.log",
            }
        }
        mock_task_manager.get_task_output = MagicMock(return_value="test output")
        mock_task_manager.get_task_exit_code = MagicMock(return_value=0)
        mock_task_manager.cleanup_task = MagicMock(return_value=True)
        mock_task_manager.list_tasks = MagicMock()

        mock_sandbox = MagicMock()

        with (
            patch(
                "aigise.toolbox.general.bash_tools_interface.get_aigise_session"
            ) as mock_get_session,
            patch(
                "aigise.toolbox.general.bash_tools_interface.get_sandbox_from_context"
            ) as mock_get_sandbox,
        ):
            mock_session = MagicMock()
            mock_session.bash_tasks = mock_task_manager
            mock_get_session.return_value = mock_session
            mock_get_sandbox.return_value = mock_sandbox

            result = get_background_task_output(
                task_id="task_123",
                tool_context=mock_context,
            )

            assert result["task_id"] == "task_123"
            assert result["output"] == "test output"
            assert result["exit_code"] == 0
            assert result["cleaned_up"] is True

    def test_get_background_task_output_not_found(self):
        """Test get_background_task_output when task doesn't exist."""
        mock_context = MagicMock()
        mock_context.state = {"aigise_session_id": "test_session"}

        mock_task_manager = MagicMock()
        mock_task_manager.tasks = {}

        with patch(
            "aigise.toolbox.general.bash_tools_interface.get_aigise_session"
        ) as mock_get_session:
            mock_session = MagicMock()
            mock_session.bash_tasks = mock_task_manager
            mock_get_session.return_value = mock_session

            result = get_background_task_output(
                task_id="nonexistent",
                tool_context=mock_context,
            )

            assert "error" in result
            assert "not found" in result["error"].lower()

    def test_get_background_task_output_no_manager(self):
        """Test get_background_task_output when no task manager exists."""
        mock_context = MagicMock()
        mock_context.state = {"aigise_session_id": "test_session"}

        with patch(
            "aigise.toolbox.general.bash_tools_interface.get_aigise_session"
        ) as mock_get_session:
            # Create a mock session without bash_tasks attribute
            # Use a regular object instead of MagicMock to avoid auto-attributes
            class MockSession:
                pass

            mock_session = MockSession()
            mock_get_session.return_value = mock_session

            result = get_background_task_output(
                task_id="task_123",
                tool_context=mock_context,
            )

            assert "error" in result
            assert "No background tasks manager" in result["error"]
