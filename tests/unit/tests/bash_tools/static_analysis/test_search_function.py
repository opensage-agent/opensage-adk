"""Unit tests for search-function bash tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from opensage.session import OpenSageSession, get_opensage_session
from opensage.toolbox.general.bash_tools_interface import run_terminal_command
from opensage.utils.project_info import PROJECT_PATH
from tests.unit.utils.utils import fix_neo4j_client

# Increase timeout for slow static analysis tests
pytestmark = pytest.mark.timeout(1200)


@pytest_asyncio.fixture(scope="module")
async def opensage_session():
    """Create opensage session for testing static analysis tools."""
    opensage_session = None
    try:
        opensage_session = get_opensage_session(
            "test-bash-tools-static-analysis-search-function",
            str(PROJECT_PATH / "tests/unit/data/configs/test_cpg.toml"),
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
async def test_search_function_basic(opensage_session: OpenSageSession):
    """Test search-function tool with basic parameters."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}
    fix_neo4j_client(opensage_session, "analysis")

    # Test search-function for a known function
    result = run_terminal_command(
        command='python3 /bash_tools/static_analysis/search-function/scripts/search_function.py "file_fsmagic"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    # Output should be plain text (returns_json: false)
    output = result["output"]
    if isinstance(output, dict):
        output = str(output)
    elif not isinstance(output, str):
        output = str(output) if output is not None else ""

    assert output is not None
    assert isinstance(output, str)
    # Should find at least one function (check for text indicating results found)
    assert "Found" in output or "function" in output.lower()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_search_function_nonexistent(opensage_session: OpenSageSession):
    """Test search-function tool with non-existent function."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}
    fix_neo4j_client(opensage_session, "analysis")

    # Test with non-existent function
    result = run_terminal_command(
        command='python3 /bash_tools/static_analysis/search-function/scripts/search_function.py "nonexistent_function_xyz123"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    # Output should be plain text (returns_json: false)
    output = result["output"]
    if isinstance(output, dict):
        output = str(output)
    elif not isinstance(output, str):
        output = str(output) if output is not None else ""

    assert output is not None
    assert isinstance(output, str)
    # Should indicate no functions found for non-existent function
    assert "No functions found" in output or len(output) == 0
