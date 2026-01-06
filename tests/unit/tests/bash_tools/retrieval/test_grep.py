"""Unit tests for grep bash tool."""

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
async def test_grep_basic_search(aigise_session: AigiseSession):
    """Test grep tool with basic pattern search."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    # Test grep with a simple pattern
    result = run_terminal_command(
        command='bash /bash_tools/retrieval/grep/scripts/grep.sh "function"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0
    assert "output" in result

    # Output is now text format (grep output)
    output = result["output"]
    assert isinstance(output, str)

    # Should find at least some matches
    lines = output.strip().splitlines() if output.strip() else []
    assert len(lines) > 0

    # Verify format: file_path:line_number:matched_line
    for line in lines:
        assert ":" in line  # Should contain at least one colon


@pytest.mark.slow
@pytest.mark.asyncio
async def test_grep_no_matches(aigise_session: AigiseSession):
    """Test grep tool with pattern that matches nothing."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    # Test grep with pattern that won't match
    result = run_terminal_command(
        command='bash /bash_tools/retrieval/grep/scripts/grep.sh "NONEXISTENT_PATTERN_XYZ123"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    # Output is now text format (grep output)
    output = result["output"]
    assert isinstance(output, str)
    # Should return empty string for no matches
    assert output.strip() == ""


@pytest.mark.slow
@pytest.mark.asyncio
async def test_grep_regex_pattern(aigise_session: AigiseSession):
    """Test grep tool with regex pattern."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    # Test grep with regex pattern
    result = run_terminal_command(
        command='bash /bash_tools/retrieval/grep/scripts/grep.sh "def.*function"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    # Output is now text format (grep output)
    output = result["output"]
    assert isinstance(output, str)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_grep_special_characters(aigise_session: AigiseSession):
    """Test grep tool with special characters in pattern."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    # Test grep with special characters (need to escape properly)
    result = run_terminal_command(
        command='bash /bash_tools/retrieval/grep/scripts/grep.sh "\\$"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    # Output is now text format (grep output)
    output = result["output"]
    assert isinstance(output, str)
