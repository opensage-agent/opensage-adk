"""Unit tests for get-callee bash tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from aigise.session import AigiseSession, get_aigise_session
from aigise.toolbox.general.bash_tools_interface import run_terminal_command
from aigise.utils.project_info import PROJECT_PATH
from tests.unit.utils.utils import fix_neo4j_client

# Increase timeout for slow static analysis tests
pytestmark = pytest.mark.timeout(1200)


@pytest_asyncio.fixture(scope="module")
async def aigise_session():
    """Create aigise session for testing static analysis tools."""
    aigise_session = None
    try:
        aigise_session = get_aigise_session(
            "test-bash-tools-static-analysis-get-callee",
            str(PROJECT_PATH / "tests/unit/data/configs/test_cpg.toml"),
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
async def test_get_callee_basic(aigise_session: AigiseSession):
    """Test get-callee tool with basic parameters."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}
    fix_neo4j_client(aigise_session, "analysis")

    # Test get-callee for a known function
    result = run_terminal_command(
        command='python3 /bash_tools/static_analysis/get-callee/scripts/get_callee.py "file_or_fd"',
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
    # Should find callees (check for text indicating results found)
    assert "Found" in output or "callee" in output.lower()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_get_callee_with_file_path(aigise_session: AigiseSession):
    """Test get-callee tool with file path parameter."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}
    fix_neo4j_client(aigise_session, "analysis")

    # Test get-callee with file path
    result = run_terminal_command(
        command='python3 /bash_tools/static_analysis/get-callee/scripts/get_callee.py "file_or_fd" --file-path "src/magic.c"',
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


@pytest.mark.slow
@pytest.mark.asyncio
async def test_get_callee_nonexistent_function(aigise_session: AigiseSession):
    """Test get-callee tool with non-existent function."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}
    fix_neo4j_client(aigise_session, "analysis")

    # Test with non-existent function
    result = run_terminal_command(
        command='python3 /bash_tools/static_analysis/get-callee/scripts/get_callee.py "nonexistent_function_xyz123"',
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
    # Should indicate no callees found for non-existent function
    assert "No callees found" in output or len(output) == 0


@pytest.mark.slow
@pytest.mark.asyncio
async def test_get_callee_uses_env_vars(aigise_session: AigiseSession):
    """Test that get-callee uses environment variables for Neo4j connection."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}
    fix_neo4j_client(aigise_session, "analysis")

    # Test without explicit Neo4j parameters (should use env vars from ~/.bashrc)
    result = run_terminal_command(
        command='python3 /bash_tools/static_analysis/get-callee/scripts/get_callee.py "file_or_fd"',
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
