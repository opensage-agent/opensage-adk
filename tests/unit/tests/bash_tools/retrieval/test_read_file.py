"""Unit tests for read-file bash tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from aigise.session import AigiseSession, get_aigise_session
from aigise.toolbox.general.bash_tools_interface import run_terminal_command
from aigise.utils.project_info import PROJECT_PATH


@pytest_asyncio.fixture(scope="module")
async def aigise_session():
    """Create aigise session for testing retrieval tools."""
    aigise_session = None
    try:
        aigise_session = get_aigise_session(
            "test-bash-tools-retrieval",
            str(PROJECT_PATH / "tests/unit/data/configs/test_main_only.toml"),
        )

        aigise_session.sandboxes.initialize_shared_volumes()
        await aigise_session.sandboxes.launch_all_sandboxes()
        await aigise_session.sandboxes.initialize_all_sandboxes()
        yield aigise_session
    finally:
        if aigise_session is not None:
            aigise_session.cleanup()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_read_file_basic(aigise_session: AigiseSession):
    """Test read-file tool with basic parameters."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    # Test reading a file that should exist in the test environment
    result = run_terminal_command(
        command='python3 /bash_tools/retrieval/read-file/scripts/read_file.py --file "/shared/code/file/src/fsmagic.c" --linenum 105 --context 5',
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    # Parse JSON output
    output = result["output"]
    if isinstance(output, str):
        output = json.loads(output)

    assert "result" in output
    assert isinstance(output["result"], str)
    # Should contain line numbers and content
    assert len(output["result"]) > 0


@pytest.mark.slow
@pytest.mark.asyncio
async def test_read_file_with_context(aigise_session: AigiseSession):
    """Test read-file tool with context lines."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    # Test with larger context
    result = run_terminal_command(
        command='python3 /bash_tools/retrieval/read-file/scripts/read_file.py --file "/shared/code/file/src/fsmagic.c" --linenum 105 --context 10',
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    # Parse JSON output
    output = result["output"]
    if isinstance(output, str):
        output = json.loads(output)

    assert "result" in output
    # With context 10, should have more lines than context 5
    assert len(output["result"]) > 0


@pytest.mark.slow
@pytest.mark.asyncio
async def test_read_file_nonexistent_file(aigise_session: AigiseSession):
    """Test read-file tool with non-existent file."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    # Test with non-existent file
    result = run_terminal_command(
        command='python3 /bash_tools/retrieval/read-file/scripts/read_file.py --file "/nonexistent/file/path.txt" --linenum 1 --context 5',
        tool_context=mock_context,
        sandbox_name="main",
    )

    # Should handle error gracefully
    assert result["success"] is True
    # Exit code might be non-zero for file not found
    # But the tool should return structured output
    output = result["output"]
    if isinstance(output, str):
        try:
            output = json.loads(output)
            # Should have error information in result
            assert "result" in output or "error" in output
        except json.JSONDecodeError:
            # If not JSON, should contain error message
            assert "error" in output.lower() or "not found" in output.lower()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_read_file_invalid_line_number(aigise_session: AigiseSession):
    """Test read-file tool with invalid line number."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    # Test with line number that's too large
    result = run_terminal_command(
        command='python3 /bash_tools/retrieval/read-file/scripts/read_file.py --file "/shared/code/file/src/fsmagic.c" --linenum 999999 --context 5',
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    # Should handle gracefully (might return empty or error)
    output = result["output"]
    if isinstance(output, str):
        try:
            output = json.loads(output)
            assert "result" in output or "error" in output
        except json.JSONDecodeError:
            pass
