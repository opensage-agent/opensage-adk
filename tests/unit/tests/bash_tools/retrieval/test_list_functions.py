"""Unit tests for list-functions bash tool."""

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
    """Create aigise session for testing list-functions tool (requires Neo4j)."""
    aigise_session = None
    try:
        aigise_session = get_aigise_session(
            "test-bash-tools-list-functions",
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
async def test_list_functions_basic(aigise_session: AigiseSession):
    """Test list-functions tool with basic parameters."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}
    fix_neo4j_client(aigise_session, "analysis")

    # Test list-functions for a known file
    result = run_terminal_command(
        command='python3 /bash_tools/retrieval/list-functions/scripts/list_functions.py --file "file/src/fsmagic.c"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    # Assert command succeeded
    assert result["success"] is True, (
        f"Command failed. Exit code: {result.get('exit_code')}, "
        f"Output: {result.get('output')}"
    )
    assert result["exit_code"] == 0

    # Check text output
    output = result["output"]
    if isinstance(output, dict):
        output = str(output)
    elif not isinstance(output, str):
        output = str(output) if output is not None else ""

    # Should contain function information
    assert output is not None
    assert len(output) > 0
    # Should contain function name or indication of results
    assert "Function:" in output or "function" in output.lower()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_list_functions_nonexistent_file(aigise_session: AigiseSession):
    """Test list-functions tool with non-existent file."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}
    fix_neo4j_client(aigise_session, "analysis")

    # Test with non-existent file
    result = run_terminal_command(
        command='python3 /bash_tools/retrieval/list-functions/scripts/list_functions.py --file "nonexistent/file/path.c"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    # Assert command succeeded
    assert result["success"] is True, (
        f"Command failed. Exit code: {result.get('exit_code')}, "
        f"Output: {result.get('output')}"
    )
    # Should handle gracefully (might return empty list or error message)
    output = result["output"]
    if isinstance(output, dict):
        output = str(output)
    elif not isinstance(output, str):
        output = str(output) if output is not None else ""

    # Should contain output (either functions found or error message)
    assert output is not None
