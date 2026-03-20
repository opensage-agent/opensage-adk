"""Unit tests for bash_tools_interface module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from opensage.toolbox.general.bash_task_manager import Task, TaskStatus
from opensage.toolbox.general.bash_tools_interface import (
    BashToolMetadata,
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
        mock_context.state = {"opensage_session_id": "test_session"}

        with (
            patch(
                "opensage.toolbox.general.bash_tools_interface.get_sandbox_from_context"
            ) as mock_get_sandbox,
            patch(
                "opensage.toolbox.general.bash_tools_interface.get_opensage_session"
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
        #  decorator catches exceptions and returns error dict
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

    def _make_mock_sandbox(
        self,
        skill_contents: dict[str, str],
        executable_skill_dirs: set[str] | None = None,
    ):
        """Build a sandbox mock that emulates find/test/cat over /bash_tools."""
        executable_skill_dirs = executable_skill_dirs or {
            p.rsplit("/SKILL.md", 1)[0] for p in skill_contents
        }
        sandbox = MagicMock()

        def _run(command, timeout=None):
            del timeout
            cmd = command[-1] if isinstance(command, list) else command

            # find <base> -type f -name SKILL.md -print
            if cmd.startswith("find ") and "-name SKILL.md -print" in cmd:
                base = cmd.split("find ", 1)[1].split(" -type f", 1)[0].strip()
                base = base.strip("'").strip('"')
                matched = [
                    p
                    for p in sorted(skill_contents)
                    if p == f"{base}/SKILL.md" or p.startswith(f"{base.rstrip('/')}/")
                ]
                return ("\n".join(matched), 0)

            # test -d <skill_dir>/scripts && find <skill_dir>/scripts ...
            if cmd.startswith("test -d ") and "/scripts && find " in cmd:
                skill_dir = cmd.split("test -d ", 1)[1].split("/scripts &&", 1)[0]
                skill_dir = skill_dir.strip("'").strip('"')
                if skill_dir in executable_skill_dirs:
                    return (f"{skill_dir}/scripts/tool.sh", 0)
                return ("", 1)

            # cat <path>
            if cmd.startswith("cat "):
                path = cmd.split("cat ", 1)[1].strip().strip("'").strip('"')
                if path in skill_contents:
                    return (skill_contents[path], 0)
                return ("", 1)

            return ("", 1)

        sandbox.run_command_in_container = MagicMock(side_effect=_run)
        return sandbox

    def test_list_available_scripts(self):
        """Test listing available scripts."""
        mock_context = MagicMock()
        skill_path = "/bash_tools/retrieval/foo/SKILL.md"
        mock_sandbox = self._make_mock_sandbox(
            {
                skill_path: "\n".join(
                    [
                        "---",
                        "name: foo",
                        "description: Foo tool",
                        "should_run_in_sandbox: main",
                        "---",
                        "",
                        "# Foo",
                    ]
                )
            }
        )

        with patch(
            "opensage.toolbox.general.bash_tools_interface.get_sandbox_from_context",
            return_value=mock_sandbox,
        ):
            result = list_available_scripts(tool_context=mock_context)

        assert isinstance(result, str)
        assert "Available Skills under /bash_tools" in result
        assert skill_path in result
        # Full SKILL.md content includes YAML frontmatter.
        assert "name:" in result
        assert "description:" in result

    def test_list_available_scripts_accepts_container_style_start_dir(self):
        mock_context = MagicMock()
        skill_path = "/bash_tools/retrieval/foo/SKILL.md"
        mock_sandbox = self._make_mock_sandbox(
            {
                skill_path: "\n".join(
                    [
                        "---",
                        "name: foo",
                        "description: Foo tool",
                        "should_run_in_sandbox: main",
                        "---",
                        "",
                        "# Foo",
                    ]
                )
            }
        )
        with patch(
            "opensage.toolbox.general.bash_tools_interface.get_sandbox_from_context",
            return_value=mock_sandbox,
        ):
            result = list_available_scripts(
                start_dir="/bash_tools/retrieval", tool_context=mock_context
            )

        assert skill_path in result
        assert "name: foo" in result

    def test_list_available_scripts_accepts_container_root_start_dir(self):
        mock_context = MagicMock()
        skill_path = "/bash_tools/retrieval/foo/SKILL.md"
        mock_sandbox = self._make_mock_sandbox(
            {
                skill_path: "\n".join(
                    [
                        "---",
                        "name: foo",
                        "description: Foo tool",
                        "should_run_in_sandbox: main",
                        "---",
                        "",
                        "# Foo",
                    ]
                )
            }
        )
        with patch(
            "opensage.toolbox.general.bash_tools_interface.get_sandbox_from_context",
            return_value=mock_sandbox,
        ):
            result = list_available_scripts(
                start_dir="/bash_tools", tool_context=mock_context
            )

        assert "Available Skills under /bash_tools" in result
        assert skill_path in result

    def test_list_available_scripts_no_tools(self):
        """Test list_available_scripts when no tools are found."""
        mock_context = MagicMock()
        mock_sandbox = self._make_mock_sandbox({})

        with patch(
            "opensage.toolbox.general.bash_tools_interface.get_sandbox_from_context",
            return_value=mock_sandbox,
        ):
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
        mock_context.state = {"opensage_session_id": "test_session"}

        with (
            patch(
                "opensage.toolbox.general.bash_tools_interface.get_sandbox_from_context"
            ) as mock_get_sandbox,
            patch(
                "opensage.toolbox.general.bash_tools_interface.get_opensage_session"
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
        mock_context.state = {"opensage_session_id": "test_session"}

        with (
            patch(
                "opensage.toolbox.general.bash_tools_interface.get_sandbox_from_context"
            ) as mock_get_sandbox,
            patch(
                "opensage.toolbox.general.bash_tools_interface.get_opensage_session"
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
        mock_context.state = {"opensage_session_id": "test_session"}
        mock_task_manager.wait_for_task.return_value = False  # Timeout

        with (
            patch(
                "opensage.toolbox.general.bash_tools_interface.get_sandbox_from_context"
            ) as mock_get_sandbox,
            patch(
                "opensage.toolbox.general.bash_tools_interface.get_opensage_session"
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
        mock_context.state = {"opensage_session_id": "test_session"}

        with patch(
            "opensage.toolbox.general.bash_tools_interface.get_sandbox_from_context"
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
        mock_context.state = {"opensage_session_id": "test_session"}
        mock_task_manager.get_task_output.return_value = '{"key": "value"}'

        with (
            patch(
                "opensage.toolbox.general.bash_tools_interface.get_sandbox_from_context"
            ) as mock_get_sandbox,
            patch(
                "opensage.toolbox.general.bash_tools_interface.get_opensage_session"
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
        mock_context.state = {"opensage_session_id": "test_session"}

        with patch(
            "opensage.toolbox.general.bash_tools_interface.get_opensage_session"
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
        mock_context.state = {"opensage_session_id": "test_session"}

        mock_task_manager = MagicMock()
        task_1 = Task(
            id="task_1",
            pid="123",
            command="echo test",
            sandbox_name="main",
            log_file="/tmp/task_task_1.log",
            exit_code_file="/tmp/task_task_1.exit",
            pid_file="/tmp/task_task_1.pid",
            cmd_file="/tmp/task_task_1.cmd.sh",
            wrapper_file="/tmp/task_task_1.wrapper.sh",
            status=TaskStatus.RUNNING,
            exit_code=None,
        )
        mock_task_manager.tasks = {"task_1": task_1}
        mock_task_manager.list_tasks = MagicMock(return_value=[task_1])

        with (
            patch(
                "opensage.toolbox.general.bash_tools_interface.get_opensage_session"
            ) as mock_get_session,
            patch(
                "opensage.toolbox.general.bash_tools_interface.get_sandbox_from_context"
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
        mock_context.state = {"opensage_session_id": "test_session"}

        mock_task_manager = MagicMock()
        task_123 = Task(
            id="task_123",
            pid="456",
            command="echo test",
            sandbox_name="main",
            log_file="/tmp/task_123.log",
            exit_code_file="/tmp/task_123.exit",
            pid_file="/tmp/task_123.pid",
            cmd_file="/tmp/task_123.cmd.sh",
            wrapper_file="/tmp/task_123.wrapper.sh",
            status=TaskStatus.COMPLETED,
            exit_code=0,
        )
        mock_task_manager.tasks = {"task_123": task_123}
        mock_task_manager.get_task_output = MagicMock(return_value="test output")
        mock_task_manager.get_task_exit_code = MagicMock(return_value=0)
        mock_task_manager.cleanup_task = MagicMock(return_value=True)
        mock_task_manager.list_tasks = MagicMock()

        mock_sandbox = MagicMock()

        with (
            patch(
                "opensage.toolbox.general.bash_tools_interface.get_opensage_session"
            ) as mock_get_session,
            patch(
                "opensage.toolbox.general.bash_tools_interface.get_sandbox_from_context"
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
        mock_context.state = {"opensage_session_id": "test_session"}

        mock_task_manager = MagicMock()
        mock_task_manager.tasks = {}

        with patch(
            "opensage.toolbox.general.bash_tools_interface.get_opensage_session"
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
        mock_context.state = {"opensage_session_id": "test_session"}

        with patch(
            "opensage.toolbox.general.bash_tools_interface.get_opensage_session"
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
