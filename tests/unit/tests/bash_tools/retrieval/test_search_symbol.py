"""Unit tests for search-symbol bash tool."""

from __future__ import annotations

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
async def test_search_symbol_basic(aigise_session: AigiseSession):
    """Test search-symbol tool with basic symbol search."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    # Test search-symbol with a known symbol
    result = run_terminal_command(
        command='bash /bash_tools/retrieval/search-symbol/scripts/search_symbol.sh "file_fsmagic"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    # Output is now text format (ctags output)
    output = result["output"]
    assert isinstance(output, str)

    # Should find at least some matches
    assert len(output.strip()) > 0
    assert "file_fsmagic" in output


@pytest.mark.slow
@pytest.mark.asyncio
async def test_search_symbol_nonexistent(aigise_session: AigiseSession):
    """Test search-symbol tool with non-existent symbol."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    # Test with non-existent symbol
    result = run_terminal_command(
        command='bash /bash_tools/retrieval/search-symbol/scripts/search_symbol.sh "NONEXISTENT_SYMBOL_XYZ123"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    # Output is now text format
    output = result["output"]
    assert isinstance(output, str)
    # Should return "No matches found." message
    assert "No matches found" in output
