"""Unit tests for search-symbol bash tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from opensage.session import OpenSageSession, get_opensage_session
from opensage.toolbox.general.bash_tools_interface import run_terminal_command
from opensage.utils.project_info import PROJECT_PATH


@pytest_asyncio.fixture(scope="module")
async def opensage_session():
    """Create opensage session for testing retrieval tools."""
    opensage_session = None
    try:
        opensage_session = get_opensage_session(
            "test-bash-tools-retrieval",
            str(PROJECT_PATH / "tests/unit/data/configs/test_main_only.toml"),
        )

        opensage_session.sandboxes.initialize_shared_volumes()
        await opensage_session.sandboxes.launch_all_sandboxes()
        await opensage_session.sandboxes.initialize_all_sandboxes()
        yield opensage_session
    finally:
        if opensage_session is not None:
            opensage_session.cleanup()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_search_symbol_basic(opensage_session: OpenSageSession):
    """Test search-symbol tool with basic symbol search."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}

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
async def test_search_symbol_nonexistent(opensage_session: OpenSageSession):
    """Test search-symbol tool with non-existent symbol."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}

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
